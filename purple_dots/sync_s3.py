"""Stream the Purple Dots platform dump from S3 into Postgres. No files.

    python sync_s3.py --check      auth and snapshot metadata
    python sync_s3.py --dry-run    stream and count, write nothing
    python sync_s3.py              stream and upsert

Run sql/create_purple_dots_s3.sql once first.

Two steps, neither using AWS credentials:
  1. POST {KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token
     grant_type=client_credentials. Must be a SYSTEM token; a user token
     gets 403 NOT_SYSTEM_CLIENT.
  2. GET {BASE_URL}/v1/campaign/dump -> three pre-signed S3 URLs, fetched
     WITHOUT an auth header.

Rows carry no names: the exporter masks age as '2***' and gender as 'D***'.
"""
import argparse
import zlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

# Before `import db`, which reads the PD_* timeouts at import time.
load_dotenv(Path(__file__).resolve().parent / ".env")

import db  # noqa: E402

DUMP_PATH = "/v1/campaign/dump"
EXPECTED_TABLES = ["user", "items", "item_actions"]
GZIP_MAGIC = b"\x1f\x8b"
TORN_SNAPSHOT_TOLERANCE = timedelta(seconds=60)
REQUEST_TIMEOUT = 30
CHUNK = 500
# Everything the platform stage needs. run_pipeline.py skips the stage when
# any is unset, rather than failing the run.
PLATFORM_SETTINGS = ("BASE_URL", "KEYCLOAK_URL", "REALM", "CLIENT_ID", "CLIENT_SECRET")


class DumpError(RuntimeError):
    """A step failed; the message is written for whoever ran the script."""


def need(name, default=None):
    value = os.getenv(name, default)
    if not value:
        raise DumpError(f"{name} is not set. Copy .env.example to .env and "
                        f"fill in {', '.join(PLATFORM_SETTINGS)} - "
                        f"the Purple Dots deployment's, not the Blue Dots one.")
    return value


def get_token():
    # No defaults for REALM / CLIENT_ID: the realm name differs per
    # environment, and a guessed one fails as an unhelpful Keycloak 404.
    url = (f"{need('KEYCLOAK_URL').rstrip('/')}/realms/"
           f"{need('REALM')}/protocol/openid-connect/token")
    r = requests.post(url, timeout=REQUEST_TIMEOUT,
                      data={"grant_type": "client_credentials",
                            "client_id": need("CLIENT_ID"),
                            "client_secret": need("CLIENT_SECRET")})
    if r.status_code != 200:
        raise DumpError(f"token request failed: {r.status_code} {r.text[:300]}")
    token = r.json().get("access_token")
    if not token:
        raise DumpError(f"no access_token in the response: {r.text[:300]}")
    return token


def get_dump(token):
    r = requests.get(need("BASE_URL").rstrip("/") + DUMP_PATH,
                     timeout=REQUEST_TIMEOUT,
                     headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 403:
        raise DumpError("403 from the dump route. The token is a coordinator "
                        "or user token; this route needs the system "
                        "(client_credentials) one. Check CLIENT_ID and "
                        "CLIENT_SECRET belong to a service account.")
    if r.status_code == 503:
        raise DumpError("503 DUMP_NOT_CONFIGURED - the deployment has no "
                        "CAMPAIGN_DUMP_INSTANCE_ID set. Nothing to fix here.")
    if r.status_code != 200:
        raise DumpError(f"dump request failed: {r.status_code} {r.text[:300]}")
    return r.json()


def check_snapshot(files, allow_torn):
    have = {f.get("table") for f in files}
    missing = [t for t in EXPECTED_TABLES if t not in have]
    if missing:
        raise DumpError(f"snapshot is missing: {', '.join(missing)}. The API "
                        f"returns all three or an error, so this is partial - "
                        f"wait for the next export.")
    stamps = [datetime.fromisoformat(f["last_modified"].replace("Z", "+00:00"))
              for f in files if f.get("last_modified")]
    if len(stamps) > 1 and max(stamps) - min(stamps) > TORN_SNAPSHOT_TOLERANCE:
        msg = (f"the three files were written {max(stamps) - min(stamps)} "
               f"apart, more than the {TORN_SNAPSHOT_TOLERANCE} tolerance. "
               f"The exporter was caught mid-write, so actions may reference "
               f"items the item file does not have yet.")
        if not allow_torn:
            raise DumpError(msg + "\nPass --allow-torn to proceed anyway.")
        print(f"  WARNING: {msg}")


def stream_records(url):
    """One parsed object per line, off the wire.

    Pre-signed, so no Authorization header - adding one makes S3 refuse.
    Files are gzip but named .jsonl, so the magic bytes are sniffed.
    """
    with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT) as resp:
        if resp.status_code != 200:
            raise DumpError(f"download failed: {resp.status_code} "
                            f"{resp.text[:200]}")
        # Decompress from iter_content rather than wrapping resp.raw:
        # urllib3 releases the connection at the end of the body, and
        # GzipFile reading its trailer from the closed socket raises
        # "read of closed file" on the last chunk.
        unzip, tail, n = None, b"", 0
        for chunk in resp.iter_content(64 * 1024):
            if not chunk:
                continue
            if unzip is None:
                unzip = (zlib.decompressobj(31)
                         if chunk[:2] == GZIP_MAGIC else False)
            if unzip is not False:
                chunk = unzip.decompress(chunk)
            tail += chunk
            while b"\n" in tail:
                line, _, tail = tail.partition(b"\n")
                n += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"    WARNING: line {n}: {exc}")
        if unzip not in (None, False):
            tail += unzip.flush()
        if tail.strip():
            try:
                yield json.loads(tail)
            except json.JSONDecodeError as exc:
                print(f"    WARNING: last line: {exc}")


def user_row(r, instance):
    # The key is "id", not "user_id" - the Blue Dots dump's spelling, which
    # this was written against before a Purple Dots record existed. There is
    # also no user_state, user_network or lifecycle_status here.
    return {
        "instance": instance, "user_id": r["id"],
        "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        "domains": r.get("domains") or [],
        "onboarded_by_org_id": r.get("onboarded_by_org_id"),
        "onboarded_via": r.get("onboarded_via"),
        "onboarded_source_id": r.get("onboarded_source_id"),
        "onboarded_at": r.get("onboarded_at"),
        "tags": r.get("tags") or {},
    }


def item_row(r, instance):
    locations = r.get("item_locations") or []
    lat, lng = (locations[0].get("lat"), locations[0].get("lng")) \
        if locations else (None, None)
    return {
        "instance": instance, "item_id": r["item_id"],
        "item_network": r.get("item_network"),
        "item_domain": r.get("item_domain"), "item_type": r.get("item_type"),
        "lifecycle_status": r.get("lifecycle_status"),
        "created_by": r.get("created_by"), "lat": lat, "lng": lng,
        "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        # Kept whole: a column per key means a migration per new field.
        "item_state": r.get("item_state") or {},
    }


# The export's item_actions allowlist (signals-s3-export manifests). Copied
# field for field: a name that is not in the dump just loads as null.
ACTION_FIELDS = (
    "partition_network", "action_type", "action_status", "update_count",
    "source_item_network", "source_item_domain", "source_item_type",
    "source_item_id", "source_item_owner",
    "target_item_network", "target_item_domain", "target_item_type",
    "target_item_id", "target_item_owner",
    "performed_by_org_id", "created_at", "updated_at",
)


def action_row(r, instance):
    """None when the record has no action_id.

    The purple_dot export allowlist carried only partition_network,
    created_at and updated_at until its item_actions columns were added, so
    every action arrived with no id. Those are counted and skipped rather
    than given a synthetic key, which would make an exporter problem look
    like data.
    """
    if not r.get("action_id"):
        return None
    row = {"instance": instance, "action_id": r["action_id"]}
    row.update({f: r.get(f) for f in ACTION_FIELDS})
    return row


# Users and items first: their ids are needed to check item_actions.
SPECS = (
    ("user",         "purple_users",   "instance,user_id",   user_row, "user_id"),
    ("items",        "purple_items",   "instance,item_id",   item_row, "item_id"),
    ("item_actions", "purple_actions", "instance,action_id", action_row, "action_id"),
)


def push(table, conflict, rows):
    """Upsert a chunk. db.upsert collapses the dump's duplicate keys, which
    Postgres rejects within one statement (21000)."""
    return db.upsert(table, rows, conflict, chunk=CHUNK, schema=db.PLATFORM_SCHEMA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="authenticate, show what the snapshot holds, exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="stream everything and count it, write nothing")
    ap.add_argument("--allow-torn", action="store_true",
                    help="proceed even if the three files disagree on time")
    ap.add_argument("--instance", default=os.getenv("PD_INSTANCE", "PD"),
                    help="value for the instance column (default: PD)")
    args = ap.parse_args()

    writing = not (args.check or args.dry_run)
    if writing and not os.getenv("DATABASE_URL"):
        raise DumpError("DATABASE_URL must be set to write. See .env.example.")
    if writing:
        db.assert_expected_database()

    dump = get_dump(get_token())
    files = dump.get("files") or []
    print(f"network {dump.get('network')}   instance {dump.get('instance')}   "
          f"expires {dump.get('expires_at')}")
    for f in files:
        size = f.get("size_bytes")
        print(f"  {str(f.get('table')):<14}"
              f"{(f'{size / 1048576:.1f} MB' if size else '?'):>10}  "
              f"{f.get('last_modified')}")
    check_snapshot(files, args.allow_torn)

    if args.check:
        print("\n--check: authenticated, snapshot looks complete.")
        return

    by_table = {f["table"]: f["url"] for f in files}
    ids = {"user_id": set(), "item_id": set()}
    dangling = 0
    print()

    for stem, table, conflict, build, id_field in SPECS:
        total, batch = 0, []
        skipped = 0
        for record in stream_records(by_table[stem]):
            row = build(record, args.instance)
            if row is None:
                skipped += 1
                continue
            if id_field in ids:
                ids[id_field].add(row[id_field])
            elif any(row.get(k) and row[k] not in ids["item_id"]
                     for k in ("source_item_id", "target_item_id")):
                # Torn snapshot: an action whose item never arrived.
                dangling += 1
            batch.append(row)
            total += 1
            if len(batch) >= CHUNK:
                if writing:
                    push(table, conflict, batch)
                batch = []
        if batch and writing:
            push(table, conflict, batch)
        print(f"  {table:<16}{total:>8} rows"
              f"{'' if writing else '  (not written)'}"
              f"{f'   {skipped} skipped: no id' if skipped else ''}")

    if dangling:
        print(f"\n  {dangling} actions reference an item not in this snapshot. "
              f"Expected if the export was mid-write.")
    if not writing:
        print("\n--dry-run: nothing written.")


if __name__ == "__main__":
    try:
        main()
    except DumpError as exc:
        raise SystemExit(f"ERROR: {exc}")
