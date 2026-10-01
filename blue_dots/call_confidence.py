"""Call confidence score: how strongly we should call a seeker NEXT.

Distinct from intent_score, which is backward-looking — what happened on a call
that already occurred. This is forward-looking: given everything we know, is
this person worth a dial right now?

Scored 0-10, where **0 means deliberately do not call**. That distinction
matters: a low score (0.5-2) is "low priority", while 0 is a decision. Scores
are floored at MIN_CALLABLE rather than clamped, so a penalty can never
silently turn into a suppression.

Priorities encoded here:
  * an explicit opt-out beats every other signal
  * someone who asked to be called back outranks everything else
  * unconverted intent outranks converted (applied) seekers
  * an untried registration outranks a number that never answers
  * intent goes stale, so recent contact scores higher

The weights are meant to be tuned; every component returns a reason string so
any score can be explained.
"""

import datetime

# --- suppressors: these force a hard 0 ---
# Taken from the drop_reason values actually present in the table, not guessed.
HARD_STOP_REASONS = {
    "said not looking",        # 1,502
    "already employed",        #   199
    "said not interested",     #    31
    "not a student of this college",   # 28 - wrong audience for the campaign
}

# A call that failed on audio never really happened, so it is a reason to
# re-dial rather than a reason to score someone down.
TECHNICAL_FAILURE_MARKERS = ("no audio", "no_audio", "speaking softly")
PROVIDER_HARD_STOP_REASONS = {
    "not hiring",
    "company closed",
    "asked not to be called",
}
UNREACHABLE_ATTEMPTS = 4   # attempts with zero answers before we give up

# anyone not explicitly suppressed stays callable
MIN_CALLABLE = 0.2

# The weights below are set so that the best possible case - they asked for a
# callback, peaked high, engaged, never applied, answers, called this fortnight -
# adds up to exactly 10.0. Nothing real ever hits the ceiling, so the ranking
# stays intact at the top instead of flattening against a clamp.
WEIGHTS = {
    # --- anyone callable starts here, so penalties still order the low end ---
    "baseline": 1.0,

    # --- the strongest signal there is: they asked ---
    "asked_to_call_later": 3.0,

    # --- unconverted intent: the conversion is still on the table ---
    "high_intent_unconverted": 2.5,
    "some_intent_unconverted": 1.25,
    "engaged_not_applied": 1.5,

    # --- untapped registrations: untried beats unresponsive ---
    "never_called_base": 3.0,
    "never_called_fresh": 1.5,    # registered within 90 days, on top of the base
    "never_called_stale": -1.0,   # registered over a year ago

    # --- contactability ---
    "contactable": 1.0,
    "per_unanswered_attempt": -0.4,   # mild: a missed call is not a refusal

    # --- recency decay: intent goes stale, so a warm number scores higher ---
    "recent_14d": 1.0,
    "recent_30d": 0.75,
    "recent_60d": 0.4,

    # --- already converted: deprioritise, do not suppress ---
    "already_applied": -2.0,

    # --- fit: needs a different-language agent ---
    "language_barrier": -1.5,

    # --- the call broke rather than the person refusing ---
    "technical_failure": 1.5,
    "dropped_after_jobs_shown": 1.5,   # saw jobs and vanished: worth one more go
}


def _days_since(value, today):
    if not value:
        return None
    try:
        return (today - datetime.date.fromisoformat(str(value)[:10])).days
    except (TypeError, ValueError):
        return None


def compute_call_confidence(row, today=None):
    """Returns (score 0-10, reasoning). `row` is an aggregated_seeker_journey row."""
    today = today or datetime.date.today()
    reason = (row.get("drop_reason") or "").strip().lower()
    called = bool(row.get("ever_called"))
    answered = bool(row.get("ever_answered"))
    attempts = int(row.get("total_calls_made") or 0)
    max_intent = float(row.get("max_intent_score") or 0)
    applied = bool(row.get("ever_applied"))

    # ---- suppressors: the only route to a real 0 ----
    # An explicit opt-out outranks everything, including a high intent score.
    # It comes from the TRRAIN service call (trrain_mastersheet.do_not_call),
    # where the seeker told us directly to stop calling.
    if row.get("trrain_do_not_call"):
        return 0.0, "do not call: seeker asked us to stop (TRRAIN)"
    if reason in HARD_STOP_REASONS:
        return 0.0, f"do not call: {reason}"
    if called and not answered and attempts >= UNREACHABLE_ATTEMPTS:
        return 0.0, f"unreachable: {attempts} attempts, never answered"

    score = WEIGHTS["baseline"]
    parts = []

    def add(key, note):
        nonlocal score
        score += WEIGHTS[key]
        parts.append(f"{note} ({WEIGHTS[key]:+g})")

    # ---- never called: untried, and worth more than an unresponsive number ----
    if not called:
        add("never_called_base", "never called")
        age = _days_since(row.get("onboarded_at"), today)
        if age is not None and age <= 90:
            add("never_called_fresh", f"registered {age}d ago")
        elif age is not None and age > 365:
            add("never_called_stale", f"registered {age}d ago")
        return _finish(score, parts)

    # ---- explicit callback request ----
    if "call later" in reason or "callback" in reason:
        add("asked_to_call_later", "asked to be called later")

    # ---- unconverted intent ----
    if not applied:
        if max_intent >= 7:
            add("high_intent_unconverted", f"peaked at intent {max_intent:g}, never applied")
        elif max_intent >= 4:
            add("some_intent_unconverted", f"peaked at intent {max_intent:g}, never applied")
        if row.get("ever_engaged"):
            add("engaged_not_applied", "engaged but did not apply")
    else:
        add("already_applied", "already applied")

    # ---- contactability ----
    if answered:
        add("contactable", "answers the phone")
    elif attempts:
        penalty = WEIGHTS["per_unanswered_attempt"] * attempts
        score += penalty
        parts.append(f"{attempts} unanswered ({penalty:+g})")

    # ---- recency ----
    age = _days_since(row.get("last_call_date"), today)
    if age is not None:
        if age <= 14:
            add("recent_14d", f"last called {age}d ago")
        elif age <= 30:
            add("recent_30d", f"last called {age}d ago")
        elif age <= 60:
            add("recent_60d", f"last called {age}d ago")

    # ---- the call broke, rather than the person refusing ----
    if any(m in reason for m in TECHNICAL_FAILURE_MARKERS):
        add("technical_failure", "last call failed on audio")
    elif "after_jobs_shown" in reason or "after jobs shown" in reason:
        add("dropped_after_jobs_shown", "dropped after seeing jobs")

    # ---- fit ----
    if "language barrier" in reason:
        add("language_barrier", "language barrier")

    return _finish(score, parts)


def _finish(score, parts):
    # floor, not clamp: penalties must never masquerade as a do-not-call
    score = max(MIN_CALLABLE, min(score, 10.0))
    return round(score, 2), " | ".join(parts) if parts else "no signal"


# ---------------------------------------------------------------- providers --
# A provider's conversion is not an application - it is confirming a vacancy or
# posting a new job. And a provider with unverified postings is worth calling
# precisely because the posting is going stale, which inverts the seeker logic:
# unconverted intent still ranks high, but "never verified" is itself the reason
# to call rather than a penalty.
PROVIDER_WEIGHTS = {
    "baseline": 1.0,
    "asked_to_call_later": 3.0,
    "unverified_postings": 2.5,     # has jobs, none confirmed active
    "mentioned_new_job": 2.0,       # said there was a new job, never posted it
    "engaged_not_updated": 1.5,     # got past phase 1, changed nothing
    "never_called_base": 3.0,
    "never_called_fresh": 1.5,
    "never_called_stale": -1.0,
    "contactable": 1.0,
    "per_unanswered_attempt": -0.4,
    "recent_14d": 1.0,
    "recent_30d": 0.75,
    "recent_60d": 0.4,
    "already_verified": -2.0,       # confirmed active recently: nothing to ask
    "language_barrier": -1.5,
}


def compute_provider_confidence(row, today=None):
    """Returns (score 0-10, reasoning). `row` is an aggregated_provider_journey row."""
    today = today or datetime.date.today()
    reason = (row.get("drop_reason") or "").strip().lower()
    called = bool(row.get("ever_called"))
    answered = bool(row.get("ever_answered"))
    attempts = int(row.get("total_calls_made") or 0)
    postings = int(row.get("total_postings") or 0)
    verified = bool(row.get("ever_verified_active"))
    phase = int(row.get("max_phase_reached") or 0)

    if reason in PROVIDER_HARD_STOP_REASONS:
        return 0.0, f"do not call: {reason}"
    if called and not answered and attempts >= UNREACHABLE_ATTEMPTS:
        return 0.0, f"unreachable: {attempts} attempts, never answered"

    score = PROVIDER_WEIGHTS["baseline"]
    parts = []

    def add(key, note):
        nonlocal score
        score += PROVIDER_WEIGHTS[key]
        parts.append(f"{note} ({PROVIDER_WEIGHTS[key]:+g})")

    if not called:
        add("never_called_base", "never called")
        age = _days_since(row.get("onboarded_at"), today)
        if age is not None and age <= 90:
            add("never_called_fresh", f"registered {age}d ago")
        elif age is not None and age > 365:
            add("never_called_stale", f"registered {age}d ago")
        if postings:
            parts.append(f"{postings} posting(s) unverified")
        return _finish(score, parts)

    if "call later" in reason or "callback" in reason:
        add("asked_to_call_later", "asked to be called later")

    if verified:
        add("already_verified", "confirmed a job active")
    elif postings:
        add("unverified_postings", f"{postings} posting(s), none confirmed active")

    if row.get("ever_new_job_mentioned") and not row.get("ever_new_job_posted"):
        add("mentioned_new_job", "mentioned a new job, never posted it")

    if phase >= 2 and not int(row.get("total_fields_updated") or 0):
        add("engaged_not_updated", f"reached phase {phase}, updated nothing")

    if answered:
        add("contactable", "answers the phone")
    elif attempts:
        penalty = PROVIDER_WEIGHTS["per_unanswered_attempt"] * attempts
        score += penalty
        parts.append(f"{attempts} unanswered ({penalty:+g})")

    age = _days_since(row.get("last_call_date"), today)
    if age is not None:
        if age <= 14:
            add("recent_14d", f"last called {age}d ago")
        elif age <= 30:
            add("recent_30d", f"last called {age}d ago")
        elif age <= 60:
            add("recent_60d", f"last called {age}d ago")

    if "language barrier" in reason:
        add("language_barrier", "language barrier")

    return _finish(score, parts)


def confidence_bucket(score):
    if score is None:
        return "n/a"
    if score == 0:
        return "0 (do not call)"
    if score <= 2:
        return "0.1-2 (low)"
    if score <= 4:
        return "2.1-4 (medium)"
    if score <= 6:
        return "4.1-6 (high)"
    return "6.1-10 (call now)"
