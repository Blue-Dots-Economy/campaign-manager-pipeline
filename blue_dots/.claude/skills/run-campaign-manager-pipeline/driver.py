#!/usr/bin/env python
"""Driver for campaign_manager_pipeline. Read-only by default.

    python .claude/skills/run-campaign-manager-pipeline/driver.py all
    python .claude/skills/run-campaign-manager-pipeline/driver.py transforms
    python .claude/skills/run-campaign-manager-pipeline/driver.py connect
    python .claude/skills/run-campaign-manager-pipeline/driver.py dryrun

WHY THIS EXISTS, AND WHY IT NEVER WRITES
----------------------------------------
This project's whole purpose is writing to a LIVE Supabase that backs real
dashboards, and to a LIVE voice-bot API. There is no staging copy. "Just run
the app to see if it works" means pushing thousands of rows into production,
so every stage here is exercised through its own --dry-run / --check path or
through pure in-memory functions.

If you need to actually push, you do that deliberately with the real command
(see SKILL.md), not through this driver.

The `transforms` subcommand needs no network and no credentials at all. It
covers the pure layer where most changes land, so it is the fast inner loop.

Windows note: stdout here is cp1252. Every print goes through say(), which
strips anything it cannot encode - otherwise a rupee sign or a Devanagari
seeker name raises UnicodeEncodeError and kills the run.
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS, FAIL = [], []


def say(msg=""):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(str(msg).encode("ascii", "replace").decode("ascii"), flush=True)


def warn(label, detail=""):
    """A known limitation, not a regression - reported, never fatal."""
    say(f"  [warn] {label}{('  ' + detail) if detail else ''}")


def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    say(f"  [{'ok ' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    return ok


# ---------------------------------------------------------------- env / deps

def cmd_check(_args):
    say("== environment ==")
    check("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0])
    for mod in ("requests", "dotenv", "openpyxl"):
        try:
            __import__(mod)
            check(f"import {mod}", True)
        except ImportError as exc:
            check(f"import {mod}", False, str(exc))

    check(".env present", os.path.exists(".env"))
    from dotenv import load_dotenv
    load_dotenv()
    for var in ("RAYA_API_KEY", "SUPABASE_URL", "SUPABASE_SECRET_KEY"):
        val = os.getenv(var)
        check(f"${var} set", bool(val), (val[:18] + "...") if val else "MISSING")

    # data/callids_to_campaignname.xlsx is read on EVERY push; a missing file
    # is a late, confusing failure rather than an obvious one.
    from pipeline import CAMPAIGN_NAME_MAPPING_PATH
    check("campaign-name sheet present", os.path.exists(CAMPAIGN_NAME_MAPPING_PATH),
          CAMPAIGN_NAME_MAPPING_PATH.replace(ROOT + os.sep, ""))
    return not FAIL


# ---------------------------------------------- pure transforms (no network)

def cmd_transforms(_args):
    """Every assertion here is a behaviour that was wrong at some point."""
    say("== transforms (pure, no network, no credentials) ==")
    import transform as T
    import transform_dkb as D
    import transform_trrain as R
    import call_confidence as C

    # Raya sends these arrays as a list, a bare dict, a JSON string, or "NA".
    check("job_list normalises a JSON string", T.job_list('[{"role":"Fitter"}]') == [{"role": "Fitter"}])
    check("job_list wraps a bare dict", T.job_list({"role": "Fitter"}) == [{"role": "Fitter"}])
    check('job_list treats "NA" as empty', T.job_list("NA") is None)
    check("job_list passes a list through", T.job_list([{"a": 1}]) == [{"a": 1}])

    # calls[0] is the LATEST attempt. A later unanswered retry used to blank
    # out what the seeker actually said on an earlier one.
    contact = {"contact_id": 1, "phone": "9999999999", "calls": [
        {"call_start_time": "2026-09-18T07:00:00Z", "call_output": {"call_answered": "No"}},
        {"call_start_time": "2026-09-17T07:00:00Z",
         "call_output": {"call_answered": "Yes", "call_engaged": "Yes", "user_intent": "Job Search"}},
    ]}
    merged = T.merged_output(contact)
    check("merged_output keeps the newest real value", merged.get("call_answered") == "No")
    check("merged_output backfills blanks from earlier attempts",
          merged.get("call_engaged") == "Yes" and merged.get("user_intent") == "Job Search")

    # requests cannot serialise a date object; both transforms must emit text.
    row = T.make_row({"id": 1, "name": "b"}, contact)
    check("make_row campaign_date is a string", isinstance(row["campaign_date"], (str, type(None))),
          repr(row["campaign_date"]))
    check("make_row is JSON-serialisable", _json_ok(row))
    check("make_row writes channel=Outbound", row.get("channel") == "Outbound")

    # True / False / None - NOT a tuple. Unpacking it raised TypeError once.
    check("classify_apply_job_tool_calls -> None with no transcript",
          T.classify_apply_job_tool_calls(None) is None)

    # DKB: the bots disagree on how to spell "empty".
    check('dkb clean() maps "unknown" to None', D.clean("unknown") is None)
    check('dkb clean() maps "Not Available" to None', D.clean("Not Available") is None)
    check("dkb whole_number rejects prose", D.whole_number("2 vacancies") is None)
    check('dkb phase_number("Phase 3") == 3', D.phase_number("Phase 3") == 3)
    check("dkb merged_output backfills", D.merged_output({"calls": [
        {"call_output": {"call_status": "unknown"}},
        {"call_output": {"call_status": "Answered"}}]}).get("call_status") == "Answered")

    # TRRAIN: "Maybe" is a real answer on a quarter of calls.
    check('trrain tristate keeps "Maybe"', R.tristate("maybe") == "Maybe")
    check('trrain tristate drops "NA"', R.tristate("NA") is None)
    tr = R.make_trrain_row({"id": 1, "name": "x"}, {"contact_id": 1, "phone": "9", "calls": [
        {"call_start_time": "2026-09-18T07:00:00Z", "call_output": {"do_not_call": "No"}},
        {"call_start_time": "2026-09-17T07:00:00Z", "call_output": {"do_not_call": "Yes"}}]})
    check("trrain do_not_call survives a later call", tr["do_not_call"] is True)
    check("trrain row is JSON-serialisable", _json_ok(tr))

    # An explicit opt-out must outrank every positive signal.
    score, reason = C.compute_call_confidence(
        {"trrain_do_not_call": True, "ever_called": True, "ever_answered": True, "max_intent_score": 9})
    check("opt-out scores 0 despite intent 9", score == 0.0, reason)
    hi, _ = C.compute_call_confidence(
        {"ever_called": True, "ever_answered": True, "max_intent_score": 9})
    check("high intent still scores well", hi > 4, str(hi))
    # 0 must mean a decision, never an arithmetic accident.
    low, _ = C.compute_call_confidence(
        {"ever_called": True, "ever_answered": False, "total_calls_made": 2})
    check("mild penalties floor above 0, never at 0", low > 0, str(low))
    return not FAIL


def _json_ok(obj):
    try:
        json.dump(obj, open(os.devnull, "w"), default=None)
        return True
    except TypeError:
        return False


# ------------------------------------------------- connectivity (read-only)

def cmd_connect(_args):
    say("== connectivity (read-only) ==")
    from dotenv import load_dotenv
    load_dotenv()
    import requests
    from raya_client import fetch_agents

    key = os.getenv("RAYA_API_KEY")
    try:
        agents, total = fetch_agents(key, limit=5)
        check("Raya /api/agent reachable", bool(agents), f"{total} agents")
    except Exception as exc:
        check("Raya /api/agent reachable", False, str(exc)[:120])

    url = os.getenv("SUPABASE_URL")
    svc = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    hdr = {"apikey": svc, "Authorization": f"Bearer {svc}", "Prefer": "count=exact"}
    for table in ("kkb_mastersheet", "dkb_mastersheet", "trrain_mastersheet",
                  "aggregated_seeker_journey", "aggregated_provider_journey",
                  "bluedot_users"):
        try:
            r = requests.get(f"{url.rstrip('/')}/rest/v1/{table}", headers=hdr,
                             params={"select": "*", "limit": 1}, timeout=60)
            n = r.headers.get("content-range", "?/?").split("/")[-1]
            check(f"{table} readable", r.status_code < 400, f"{n} rows")
        except Exception as exc:
            check(f"{table} readable", False, str(exc)[:100])

    # anon must NOT be able to read anything. This is a security assertion.
    anon = os.getenv("SUPABASE_PUBLISHABLE_KEY")
    if anon:
        exposed = []
        for table in ("kkb_mastersheet", "aggregated_seeker_journey", "app_users"):
            r = requests.get(f"{url.rstrip('/')}/rest/v1/{table}",
                             headers={"apikey": anon, "Authorization": f"Bearer {anon}"},
                             params={"select": "*", "limit": 1}, timeout=60)
            if r.status_code < 400 and r.json():
                exposed.append(table)
        check("anon key cannot read any table", not exposed, ", ".join(exposed) or "all denied")
    return not FAIL


# --------------------------------------------------------- dry-run each stage

STAGES = [
    ("column preflight", [sys.executable, "-u", "-c",
                          "import main,os;main.check_columns(os.getenv('SUPABASE_URL'),"
                          "os.getenv('SUPABASE_SECRET_KEY'));print('columns match transform.py')"]),
    ("seeker rollup",    [sys.executable, "-u", "build_seeker_journey.py", "--dry-run"]),
    ("confidence score", [sys.executable, "-u", "score_confidence.py", "--dry-run", "--table", "seeker"]),
    ("provider rollup",  [sys.executable, "-u", "build_provider_journey.py", "--dry-run"]),
]


def cmd_dryrun(args):
    say("== dry runs (nothing is written) ==")
    from dotenv import load_dotenv
    load_dotenv()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for label, cmd in STAGES:
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=args.timeout, env=env)
        tail = [l for l in (p.stdout or "").strip().split("\n") if l.strip()][-1:] or [""]
        ok = p.returncode == 0
        check(label, ok, f"{time.time() - t0:.0f}s  {tail[0][:90]}")
        if not ok:
            say("        " + (p.stderr or "").strip().split("\n")[-1][:200])
    return not FAIL


# ------------------------------------------------------------------- driver

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=["check", "transforms", "connect", "dryrun", "all"])
    ap.add_argument("--timeout", type=int, default=1200,
                    help="per-stage seconds; the rollups read ~130k rows")
    args = ap.parse_args()

    stages = {"check": cmd_check, "transforms": cmd_transforms, "connect": cmd_connect,
              "dryrun": cmd_dryrun}
    order = ["check", "transforms", "connect", "dryrun"] if args.stage == "all" else [args.stage]

    t0 = time.time()
    for name in order:
        stages[name](args)
        say()
    say(f"== {len(PASS)} passed, {len(FAIL)} failed in {time.time() - t0:.0f}s ==")
    if FAIL:
        for f in FAIL:
            say(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
