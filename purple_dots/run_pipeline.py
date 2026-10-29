"""The whole Purple Dots pipeline.

    python run_pipeline.py --dry-run
    python run_pipeline.py

Stages: check, outbound, inbound. A failed check stops the run; any other
failure leaves the remaining stages to run. Exit code is the number of
stages that failed.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent

# The stages load this themselves; this copy lets PD_REFRESH_DAYS in .env set
# the --refresh-days default.
load_dotenv(HERE / ".env")


def run(label, argv, dry_run):
    """One stage. Returns (ok, seconds, note)."""
    started = time.time()
    print()
    print("=" * 72)
    print(f"  {label}")
    print("=" * 72)
    command = [sys.executable, *argv] + (["--dry-run"] if dry_run else [])
    try:
        result = subprocess.run(command, cwd=HERE)
        code = result.returncode
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"  could not start: {exc}")
        return False, time.time() - started, str(exc)[:60]
    return code == 0, time.time() - started, "" if code == 0 else f"exit {code}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="rehearse every stage; nothing is written")
    ap.add_argument("--refresh-days", type=int,
                    default=int(os.getenv("PD_REFRESH_DAYS", "3")),
                    metavar="N",
                    help="re-fetch stored calls from the last N days, so a "
                         "call captured mid-call is corrected. 0 disables. "
                         "Default 3, or PD_REFRESH_DAYS.")
    ap.add_argument("--skip", action="append", default=[],
                    metavar="STAGE",
                    choices=["check", "outbound", "inbound"],
                    help="leave a stage out; repeatable")
    args = ap.parse_args()

    refresh = (["--refresh-days", str(args.refresh_days)]
               if args.refresh_days else [])
    print(f"Purple Dots pipeline  {'(dry run)' if args.dry_run else ''}"
          + (f"  refreshing the last {args.refresh_days} days"
             if refresh else ""))

    results = []

    # No --dry-run: the check only reads.
    if "check" not in args.skip:
        ok, secs, note = run("1/3  check - connections and tables",
                             ["load_purple.py", "--check"], dry_run=False)
        results.append(("check", ok, secs, note))
        if not ok:
            print("\n  check failed - stopping before anything is fetched.")
            _summary(results)
            return 1

    if "outbound" not in args.skip:
        results.append(("outbound", *run(
            "2/3  outbound - calls the bots made",
            ["load_purple.py"] + refresh, args.dry_run)))

    if "inbound" not in args.skip:
        results.append(("inbound", *run(
            "3/3  inbound - calls people made in",
            ["load_purple.py", "--inbound"] + refresh, args.dry_run)))

    return _summary(results)


def _summary(results):
    print()
    print("=" * 72)
    failed = [name for name, ok, _, _ in results if not ok]
    for name, ok, secs, note in results:
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {name:<10}{secs:>6.0f}s   {note}")
    print()
    if failed:
        print(f"{len(failed)} stage(s) failed: {', '.join(failed)}")
    else:
        print("all stages completed")
    return len(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\ninterrupted - whatever had already been written stays.")
