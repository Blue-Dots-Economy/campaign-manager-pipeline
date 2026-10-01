"""Fetch the latest non-PII campaign dump (user, items, item_actions).

Implements the two-step flow from curl.yaml:
  1. POST {KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token
     with grant_type=client_credentials  -> access_token
  2. GET  {BASE_URL}/v1/campaign/dump  with Authorization: Bearer <token>
     -> three pre-signed S3 URLs, downloaded WITHOUT an auth header.

Configure via environment variables (or a .env file next to this script):
  BASE_URL        e.g. https://campaign.example.org
  KEYCLOAK_URL    e.g. https://auth.example.org
  REALM           e.g. campaign            (default: campaign)
  CLIENT_ID       e.g. campaign-manager    (default: campaign-manager)
  CLIENT_SECRET   the client secret
  OUT_DIR         download directory       (default: ./dumps)

Usage:
  python fetchdata.py                 # fetch metadata + download the three files
  python fetchdata.py --no-download   # just print the dump metadata
  python fetchdata.py --allow-torn    # download even if last_modified values disagree
  python fetchdata.py --env Dharwad.postman_environment.json --out-dir dumps/dharwad
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DUMP_PATH = "/v1/campaign/dump"
EXPECTED_TABLES = ["user", "items", "item_actions"]
# Timestamps further apart than this mean the exporter's run was caught mid-write.
TORN_SNAPSHOT_TOLERANCE = timedelta(seconds=60)
REQUEST_TIMEOUT = 30
DOWNLOAD_CHUNK = 1024 * 1024


class DumpError(RuntimeError):
    """A step of the flow failed; the message is meant for the user."""


# Postman environment key -> the env var this script reads.
POSTMAN_KEYS = {
    "base_url": "BASE_URL",
    "keycloak_url": "KEYCLOAK_URL",
    "realm": "REALM",
    "client_id": "CLIENT_ID",
    "client_secret": "CLIENT_SECRET",
}


def load_postman_env(path):
    """Populate os.environ from a Postman environment export; returns its name.

    Takes precedence over .env, so --env picks the deployment for this run.
    """
    path = Path(path)
    if not path.exists():
        raise DumpError(f"No such environment file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("values", []):
        name = POSTMAN_KEYS.get(entry.get("key"))
        value = (entry.get("value") or "").strip()
        if name and value and entry.get("enabled", True):
            os.environ.setdefault(name, value)
    return data.get("name") or path.stem


def load_dotenv(path=SCRIPT_DIR / ".env"):
    """Populate os.environ from a simple KEY=VALUE file, if one exists."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def require_env(name, default=None):
    value = os.environ.get(name, default)
    if not value:
        raise DumpError(f"Environment variable {name} is not set (see the docstring in fetchdata.py).")
    return value


def get_system_token(keycloak_url, realm, client_id, client_secret):
    """Step 1: client_credentials grant. Returns (access_token, expires_in)."""
    url = f"{keycloak_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"
    response = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise DumpError(f"Token request failed ({response.status_code}): {response.text[:500]}")

    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise DumpError(f"Token response had no access_token: {payload}")
    return token, payload.get("expires_in", 0)


def get_dump(base_url, token):
    """Step 2: fetch the dump metadata with the system token."""
    url = f"{base_url.rstrip('/')}{DUMP_PATH}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        # The API's documented failures (403/404/503) all carry {"error": {...}}.
        try:
            err = response.json().get("error", {})
            detail = f"{err.get('code', '?')}: {err.get('detail', '')} {err.get('fields', '')}".strip()
        except ValueError:
            detail = response.text[:500]
        raise DumpError(f"Dump request failed ({response.status_code}) {detail}")
    return response.json()


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_snapshot(dump):
    """Validate the three files are present and from the same exporter run.

    Returns a warning string, or None when the snapshot looks consistent.
    """
    files = dump.get("files", [])
    tables = [f.get("table") for f in files]
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    if missing:
        raise DumpError(f"Dump is missing objects: {', '.join(missing)}")

    stamps = [parse_ts(f["last_modified"]) for f in files]
    spread = max(stamps) - min(stamps)
    if spread > TORN_SNAPSHOT_TOLERANCE:
        return (
            f"last_modified values span {spread} — the snapshot may be torn "
            "(mixed exporter runs)."
        )
    return None


def download(file_entry, out_dir):
    """Download one pre-signed URL. No Authorization header: it breaks the signature."""
    dest = out_dir / f"{file_entry['table']}.jsonl"
    with requests.get(file_entry["url"], stream=True, timeout=REQUEST_TIMEOUT) as response:
        if response.status_code != 200:
            raise DumpError(
                f"Download of {file_entry['table']} failed ({response.status_code}): "
                f"{response.text[:300]}"
            )
        written = 0
        with open(dest, "wb") as handle:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK):
                handle.write(chunk)
                written += len(chunk)

    expected = file_entry.get("size_bytes")
    if expected and written != expected:
        raise DumpError(
            f"{file_entry['table']}: downloaded {written} bytes, expected {expected}."
        )
    return dest, written


def main():
    parser = argparse.ArgumentParser(description="Fetch the latest non-PII campaign dump.")
    parser.add_argument("--no-download", action="store_true", help="Only print the dump metadata.")
    parser.add_argument("--allow-torn", action="store_true",
                        help="Download even if last_modified values disagree.")
    parser.add_argument("--out-dir", help="Where to write the .jsonl files (default: $OUT_DIR or ./dumps).")
    parser.add_argument("--env", help="Postman environment JSON to read hosts and secret from.")
    args = parser.parse_args()

    if args.env:
        print(f"Environment: {load_postman_env(args.env)}")
    load_dotenv()

    base_url = require_env("BASE_URL")
    keycloak_url = require_env("KEYCLOAK_URL")
    realm = require_env("REALM", "campaign")
    client_id = require_env("CLIENT_ID", "campaign-manager")
    client_secret = require_env("CLIENT_SECRET")
    out_dir = Path(args.out_dir or os.environ.get("OUT_DIR") or SCRIPT_DIR / "dumps")

    token, expires_in = get_system_token(keycloak_url, realm, client_id, client_secret)
    print(f"Got system token (expires in {expires_in}s).")

    dump = get_dump(base_url, token)
    print(json.dumps(dump, indent=2))
    print(
        f"\nnetwork={dump.get('network')} instance={dump.get('instance')} "
        f"urls expire at {dump.get('expires_at')}"
    )

    warning = check_snapshot(dump)
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
        if not args.allow_torn and not args.no_download:
            raise DumpError("Refusing to download a torn snapshot; re-run later or pass --allow-torn.")

    if args.no_download:
        return

    expires_at = dump.get("expires_at")
    if expires_at and parse_ts(expires_at) <= datetime.now(timezone.utc):
        raise DumpError("The pre-signed URLs have already expired; re-run to get fresh ones.")

    out_dir.mkdir(parents=True, exist_ok=True)
    by_table = {f["table"]: f for f in dump["files"]}
    for table in EXPECTED_TABLES:
        dest, written = download(by_table[table], out_dir)
        print(f"  {table}: {written:,} bytes -> {dest}")

    print(f"\nDone. Files in {out_dir}")


if __name__ == "__main__":
    try:
        main()
    except DumpError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"ERROR: network failure: {exc}", file=sys.stderr)
        sys.exit(1)
