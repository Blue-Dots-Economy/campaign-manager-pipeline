"""The whole Purple Dots pipeline, in one command.

    python run_pipeline.py --dry-run     # rehearse every stage, write nothing
    python run_pipeline.py               # the real thing

Four stages, in order:

    1. check     every connection and table, before anything is fetched
    2. outbound  calls the bots made, via batches
    3. inbound   calls people made to the bots
    4. platform  the S3 dump: users, items, actions

STAGES SKIP THEMSELVES WHEN THEY CANNOT RUN
Stage 4 needs campaign-manager credentials that are not configured yet, so
it reports 'skipped' and the run still succeeds. The alternative - failing
the whole pipeline because one optional source is not set up - teaches
people to ignore the exit code, which is the one thing it must not do.

A STAGE FAILING DOES NOT STOP THE REST
Inbound failing should not cost you the outbound calls; they are separate
sources writing separate rows. The exit code is the number of stages that
failed, and the summary says which.

Stage 1 is the exception. If nothing is reachable there is no point
fetching for twenty minutes to find out, so a failed check stops the run.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent

# The stages load this themselves; this copy is for the "is stage 4
# configured?" test below, which happens before any of them start. Without
# it the platform stage reports itself unconfigured while sitting next to a
# .env that configures it.
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
    ap.add_argument("--skip", action="append", default=[],
                    metavar="STAGE",
                    choices=["check", "outbound", "inbound", "platform"],
                    help="leave a stage out; repeatable")
    args = ap.parse_args()

    print(f"Purple Dots pipeline  {'(dry run)' if args.dry_run else ''}")

    results = []

    # 1. Check. Not given --dry-run: it only ever reads, and a rehearsed
    #    connection check would be worth nothing.
    if "check" not in args.skip:
        ok, secs, note = run("1/4  check - connections and tables",
                             ["load_purple.py", "--check"], dry_run=False)
        results.append(("check", ok, secs, note))
        if not ok:
            print("\n  check failed - stopping before anything is fetched.")
            _summary(results)
            return 1

    if "outbound" not in args.skip:
        results.append(("outbound", *run("2/4  outbound - calls the bots made",
                                         ["load_purple.py"], args.dry_run)))

    if "inbound" not in args.skip:
        results.append(("inbound", *run("3/4  inbound - calls people made in",
                                        ["load_purple.py", "--inbound"],
                                        args.dry_run)))

    # 4. Platform. Optional: the campaign-manager credentials are separate
    #    from the Raya and database ones and may not exist on this machine.
    if "platform" not in args.skip:
        missing = [n for n in ("BASE_URL", "KEYCLOAK_URL", "CLIENT_SECRET")
                   if not os.getenv(n)]
        if missing:
            print()
            print("=" * 72)
            print("  4/4  platform - S3 dump")
            print("=" * 72)
            print(f"  skipped: {', '.join(missing)} not set. See .env.example.")
            results.append(("platform", True, 0.0, "skipped - not configured"))
        else:
            results.append(("platform", *run("4/4  platform - S3 dump",
                                             ["sync_s3.py"], args.dry_run)))

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
