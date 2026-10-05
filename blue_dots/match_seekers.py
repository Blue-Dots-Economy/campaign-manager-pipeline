"""Match score for every Blue Dot seeker against the unpaused jobs.

    python match_seekers.py --dry-run
    python match_seekers.py
    python match_seekers.py --top 3 --instance KA

Four signals, chosen because they are the only ones present on BOTH sides:

    role           5 points   seeker 48% filled, job 99%
    distance       3 points   real coordinates only
    qualification  2 points   same vocabulary on both sides
    experience    +/-1        fresher vs worked-before

Scored 0-10. A pair scores None - no row at all - when role is unknown AND
the coordinates are unusable, because with neither of those there is nothing
left to be confident about. Returning 0 would look like "bad match"; no row
says "cannot tell", which is the honest answer for about a fifth of seekers.

WHAT IS DELIBERATELY NOT USED
age and gender are masked in bluedot_items ('2***', 'M***'). Age is knowable
only to the decade, so it cannot reliably exclude minors - which matters,
since 34 applications failed on MINOR_ACTION_CHANNEL_BLOCKED. And there is no
gender requirement on jobs in the database at all: female_only exists in the
Operation Rozgar workbook but never reached item_state. Scoring on either
would be guessing.
"""
import argparse
import collections
import heapq
import math
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Both sides use these to mean "no preference".
WILDCARD = {"any", "anything", "any job", "all", "na", "n/a", "none"}

# Education, as a ladder. Both sides draw from the same vocabulary, which is
# the only reason this comparison is possible at all - job minEducational-
# Institute values are None/School/ITI/Diploma/College, and the seeker's
# educationCategory uses the same words plus a few finer ones.
EDUCATION = {
    "none": 0, "learned informally": 0,
    "school": 1,
    "iti": 2, "other vocational training": 2, "certification": 2,
    "diploma": 3, "polytechnic": 3, "pu college": 3,
    "college": 4,
}
# Words that carry no matching signal on their own.
STOPWORDS = {"other", "operator", "executive", "assistant", "associate",
             "trainee", "worker", "member", "staff", "person", "and", "of"}

EARTH_KM = 6371.0


def norm(value):
    """Lowercase, split on the '|' both sides use for multiple values."""
    if value is None:
        return []
    text = str(value).strip().lower()
    if not text:
        return []
    return [p.strip() for p in text.split("|") if p.strip()]


def tokens(phrase):
    """Significant words. 'Machine Operator' and 'Machine operator' are the
    same job; 'ITI (Other)' carries only 'iti'."""
    words = re.findall(r"[a-z]+", phrase)
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def role_points(seeker_role, job_role):
    """5 exact, 3 shared token, 1 wildcard, 0 otherwise."""
    svals, jvals = norm(seeker_role), norm(job_role)
    if not svals or not jvals:
        return 0, None
    s_wild = any(v in WILDCARD for v in svals)
    j_wild = any(v in WILDCARD for v in jvals)

    for s in svals:
        for j in jvals:
            if s == j and s not in WILDCARD:
                return 5, f"role exact '{s}' (+5)"
    for s in svals:
        for j in jvals:
            shared = tokens(s) & tokens(j)
            if shared:
                return 3, f"role overlap '{'/'.join(sorted(shared))}' (+3)"
    if s_wild or j_wild:
        # "Any" is a real answer, but a weak one - it says nothing about fit
        return 1, "role open to anything (+1)"
    return 0, None


def km_between(a_lat, a_lng, b_lat, b_lng):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def distance_points(seeker, job):
    """Only where BOTH have real coordinates. is_default_geo marks the known
    sentinel values - 21% of seekers carry them, and treating those as real
    would pile thousands of people onto one point."""
    if seeker.get("is_default_geo") or job.get("is_default_geo"):
        return 0, None
    if None in (seeker.get("lat"), seeker.get("lng"), job.get("lat"), job.get("lng")):
        return 0, None
    km = km_between(seeker["lat"], seeker["lng"], job["lat"], job["lng"])
    if km <= 5:
        return 3, f"{km:.0f}km away (+3)"
    if km <= 15:
        return 2, f"{km:.0f}km away (+2)"
    if km <= 30:
        return 1, f"{km:.0f}km away (+1)"
    return -1, f"{km:.0f}km away (-1)"


def job_requirement(state):
    """The job's minimum education, from whichever of the five keys it used."""
    direct = str(state.get("minEducationalInstitute") or "").strip().lower()
    if direct in EDUCATION:
        return EDUCATION[direct], direct
    if state.get("minQualificationCollege"):
        return EDUCATION["college"], "college"
    if state.get("minQualificationPolytechnic"):
        return EDUCATION["polytechnic"], "polytechnic"
    if state.get("minQualificationVocational"):
        return EDUCATION["iti"], "vocational"
    if state.get("minQualificationSchool"):
        return EDUCATION["school"], "school"
    return None, None


def qualification_points(seeker_state, job_state):
    need, label = job_requirement(job_state)
    if need is None:
        return 0, None
    have_raw = str(seeker_state.get("educationCategory") or "").strip().lower()
    have = EDUCATION.get(have_raw)
    if have is None:
        return 0, None
    if have >= need:
        return 2, f"qualified ({have_raw} >= {label}) (+2)"
    return -2, f"under-qualified ({have_raw} < {label}) (-2)"


def experience_points(seeker_state, job_state):
    want = str(job_state.get("candidateExperienceType") or "").strip().lower()
    if not want or want == "no preference":
        return 0, None
    raw = str(seeker_state.get("workExperience") or "").strip().lower()
    if not raw:
        return 0, None
    fresher = raw in WILDCARD or raw in {"fresher", "no experience", "0", "none"}
    if want == "fresher" and fresher:
        return 1, "fresher, as wanted (+1)"
    if want == "worked before" and not fresher:
        return 1, "has experience, as wanted (+1)"
    return -1, f"wants {want} (-1)"


def score(seeker, job):
    """(score, reason) or (None, None) when there is nothing to judge on."""
    s_state = seeker.get("item_state") or {}
    j_state = job.get("item_state") or {}

    role, role_why = role_points(s_state.get("nameOfJobRolesInterestedIn"),
                                 j_state.get("role"))
    dist, dist_why = distance_points(seeker, job)

    # no role and no geography = no basis for a number
    if role_why is None and dist_why is None:
        return None, None

    qual, qual_why = qualification_points(s_state, j_state)
    exp, exp_why = experience_points(s_state, j_state)

    total = role + dist + qual + exp
    why = " | ".join(w for w in (role_why, dist_why, qual_why, exp_why) if w)
    return max(0.0, min(10.0, float(total))), why


def page(url, key, params):
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        resp = requests.get(f"{url}/rest/v1/bluedot_items", headers=headers,
                            timeout=300,
                            params={**params, "offset": offset, "limit": 1000})
        chunk = resp.json()
        if not isinstance(chunk, list) or not chunk:
            break
        rows += chunk
        if len(chunk) < 1000:
            break
        offset += len(chunk)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--top", type=int, default=5, help="matches kept per seeker")
    # Higher than --top on purpose: a seeker needs a handful of options, but a
    # vacancy needs a shortlist, and there are ~21,000 eligible seekers per job.
    ap.add_argument("--per-job", type=int, default=20, dest="per_job",
                    help="candidates kept per job (default 20)")
    ap.add_argument("--instance", help="UP or KA; both by default")
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    select = "instance,item_id,lat,lng,is_default_geo,item_state"

    extra = {"instance": f"eq.{args.instance}"} if args.instance else {}
    seekers = page(url, key, {"select": select, "item_domain": "eq.seeker",
                              "order": "item_id", **extra})
    jobs = page(url, key, {"select": select, "item_domain": "eq.provider",
                           "pause_status": "eq.N", "order": "item_id", **extra})
    print(f"{len(seekers)} seekers x {len(jobs)} unpaused jobs")

    # A seeker in UP cannot take a job in KA: the instances are separate
    # programmes, and a cross-instance match would be nonsense.
    jobs_by_instance = collections.defaultdict(list)
    for job in jobs:
        jobs_by_instance[job["instance"]].append(job)
    print(f"  jobs per instance: "
          f"{ {k: len(v) for k, v in sorted(jobs_by_instance.items())} }")

    # Two shortlists from one pass. The seeker side is local and immediate;
    # the job side needs a running top-N, so each job keeps a small min-heap
    # capped at --top. That is 115 heaps of 5 rather than the ~4.4M scored
    # pairs a single in-memory list would need.
    keep, unscorable = {}, 0
    per_job = collections.defaultdict(list)
    for seeker in seekers:
        scored = []
        for job in jobs_by_instance.get(seeker["instance"], ()):
            value, why = score(seeker, job)
            if value is None:
                continue
            scored.append((value, job["item_id"], why))
            pair = (seeker["instance"], seeker["item_id"], job["item_id"])
            heap = per_job[(seeker["instance"], job["item_id"])]
            if len(heap) < args.per_job:
                heapq.heappush(heap, (value, pair, why))
            elif value > heap[0][0]:
                heapq.heapreplace(heap, (value, pair, why))
        if not scored:
            unscorable += 1
            continue
        scored.sort(key=lambda x: -x[0])
        for rank, (value, job_id, why) in enumerate(scored[:args.top], start=1):
            pair = (seeker["instance"], seeker["item_id"], job_id)
            keep[pair] = {"instance": pair[0], "seeker_item_id": pair[1],
                          "job_item_id": pair[2], "match_score": value,
                          "match_reason": why, "match_rank": rank,
                          "job_rank": None}

    # now fold in each job's own shortlist, adding rows the seeker pass missed
    added = 0
    for heap in per_job.values():
        for rank, (value, pair, why) in enumerate(
                sorted(heap, key=lambda x: -x[0]), start=1):
            row = keep.get(pair)
            if row is None:
                added += 1
                keep[pair] = {"instance": pair[0], "seeker_item_id": pair[1],
                              "job_item_id": pair[2], "match_score": value,
                              "match_reason": why, "match_rank": None,
                              "job_rank": rank}
            else:
                row["job_rank"] = rank
    print(f"  rows added by the per-job shortlist: {added}")

    rows = list(keep.values())
    spread = collections.Counter(int(r["match_score"]) for r in rows)
    matched = len({(r["instance"], r["seeker_item_id"]) for r in rows})
    print(f"\n  seekers with at least one match : {matched} "
          f"({100 * matched / max(len(seekers), 1):.0f}%)")
    print(f"  seekers with NO basis to score  : {unscorable} "
          f"({100 * unscorable / max(len(seekers), 1):.0f}%)  - no role, no usable coords")
    print(f"  match rows to write             : {len(rows)}")
    print("\n  score spread (all kept matches)")
    for value in sorted(spread, reverse=True):
        print(f"    {value:>3}  {spread[value]:>7}")

    best = sorted(rows, key=lambda r: -r["match_score"])[:5]
    # the asymmetry this per-job pass exists to fix
    covered = collections.Counter(
        (r["instance"], r["job_item_id"]) for r in rows)
    thin = sum(1 for n in covered.values() if n < 10)
    print(f"\n  jobs with at least one candidate : {len(covered)} of {len(jobs)}")
    print(f"  jobs with fewer than 10          : {thin}")

    print("\n  strongest matches")
    for r in best:
        print(f"    {r['match_score']:>4}  {r['instance']}  "
              f"{r['seeker_item_id'][:12]}..  {r['match_reason'][:88]}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    endpoint = f"{url}/rest/v1/seeker_job_matches"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    done = 0
    for start in range(0, len(rows), 500):
        batch = rows[start:start + 500]
        # 190k rows is a long enough write that the connection WILL drop at
        # least once - it reset at 90,500 the first time. Same retry shape as
        # supabase_client.push_to_supabase, and the upsert makes each chunk
        # idempotent, so a retry can never double-write.
        last = ""
        for attempt in range(5):
            try:
                resp = requests.post(
                    endpoint, headers=headers, timeout=300,
                    params={"on_conflict": "instance,seeker_item_id,job_item_id"},
                    json=batch)
            except requests.exceptions.RequestException as exc:
                last = str(exc)[:120]
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code in (502, 503, 504):
                last = resp.text[:120]
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise SystemExit(f"write failed at {start}: "
                                 f"{resp.status_code} {resp.text[:300]}")
            break
        else:
            raise SystemExit(f"write failed at {start} after 5 attempts: {last}")
        done += len(batch)
        if start % 10000 == 0 or done == len(rows):
            print(f"    {done}/{len(rows)}")
    print(f"\n  wrote {done} match rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
