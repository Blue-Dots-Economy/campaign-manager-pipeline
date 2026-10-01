"""Thin client for pushing rows into Supabase via the PostgREST API.

Every function takes a `table` argument so the same code serves both
kkb_mastersheet (seeker calls) and dkb_mastersheet (employer calls).
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from requests.adapters import HTTPAdapter


def fetch_pushed_batch_ids(supabase_url: str, supabase_key: str, page_size: int = 1000, table: str = "kkb_mastersheet") -> set[str]:
    """Return the distinct `batch_id` (Raya batch id) values already present
    in kkb_mastersheet, so callers can tell which batches are already pushed."""
    url = f"{supabase_url.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }

    seen: set[str] = set()
    offset = 0
    while True:
        resp = requests.get(
            url,
            headers=headers,
            params={"select": "batch_id", "order": "id", "offset": offset, "limit": page_size},
            timeout=60,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Failed to fetch existing batch_ids: {resp.status_code} {resp.text}")
        page = resp.json()
        if not page:
            break
        for row in page:
            sid = row.get("batch_id")
            if sid is not None:
                seen.add(str(sid))
        if len(page) < page_size:
            break
        offset += len(page)
    return seen


def patch_by_call_ids(
    supabase_url: str,
    supabase_key: str,
    call_ids: list[str],
    updates: dict[str, Any],
    chunk_size: int = 200,
    table: str = "kkb_mastersheet",
) -> None:
    """PATCH the given `updates` onto every row whose call_id is in call_ids,
    batched to keep each request's filter list a reasonable size."""
    if not call_ids:
        return

    url = f"{supabase_url.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }

    for start in range(0, len(call_ids), chunk_size):
        chunk = call_ids[start:start + chunk_size]
        id_list = ",".join(chunk)
        resp = requests.patch(
            url,
            headers=headers,
            params={"call_id": f"in.({id_list})"},
            json=updates,
            timeout=60,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Patch failed for {len(chunk)} call_ids: {resp.status_code} {resp.text}")


def patch_per_call_id(
    supabase_url: str,
    supabase_key: str,
    updates_by_call_id: dict[str, dict[str, Any]],
    max_workers: int = 20,
    progress: Any = None,
    table: str = "kkb_mastersheet",
) -> list[str]:
    """PATCHes a distinct `updates` dict onto every row matching each call_id
    (there is no unique constraint on call_id in kkb_mastersheet — some
    call_ids have 2-3 duplicate rows — so this correctly updates all copies).
    One request per call_id, run concurrently. Returns the list of call_ids
    that failed (so the caller can report/retry) instead of raising, since a
    single bad call_id shouldn't abort a 20k-row backfill.

    progress: optional callable(done, total).
    """
    if not updates_by_call_id:
        return []

    url = f"{supabase_url.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    def _patch_one(call_id: str, updates: dict[str, Any], max_retries: int = 3) -> tuple[str, bool, str]:
        last_err = ""
        for attempt in range(max_retries + 1):
            try:
                resp = session.patch(url, headers=headers, params={"call_id": f"eq.{call_id}"}, json=updates, timeout=60)
            except requests.exceptions.RequestException as exc:
                # dropped connections / 502s from the proxy — retry rather than
                # letting this kill the whole backfill (this is what crashed
                # the run last time: an unhandled ConnectionResetError).
                last_err = str(exc)
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code == 502:
                last_err = resp.text[:300]
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                return call_id, False, resp.text[:300]
            return call_id, True, ""
        return call_id, False, last_err

    failed: list[str] = []
    total = len(updates_by_call_id)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_call_id = {executor.submit(_patch_one, cid, upd): cid for cid, upd in updates_by_call_id.items()}
        for future in as_completed(future_to_call_id):
            try:
                call_id, ok, err = future.result()
            except Exception as exc:  # belt-and-suspenders: never let one row kill the whole backfill
                call_id, ok, err = future_to_call_id[future], False, str(exc)
            if not ok:
                failed.append(call_id)
                print(f"  PATCH failed for call_id={call_id}: {err}")
            done += 1
            if progress:
                progress(done, total)
    return failed


def fetch_existing_call_ids(supabase_url: str, supabase_key: str, page_size: int = 1000, table: str = "kkb_mastersheet") -> set[str]:
    """Every call_id currently in kkb_mastersheet — used to keep bulk updates
    from inserting skeleton rows for calls that were never pushed."""
    url = f"{supabase_url.rstrip('/')}/rest/v1/{table}"
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    seen: set[str] = set()
    offset = 0
    while True:
        resp = requests.get(
            url,
            headers=headers,
            params={"select": "call_id", "order": "call_id", "offset": offset, "limit": page_size},
            timeout=90,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Failed to fetch call_ids: {resp.status_code} {resp.text}")
        page = resp.json()
        if not page:
            break
        for row in page:
            if row.get("call_id"):
                seen.add(str(row["call_id"]))
        if len(page) < page_size:
            break
        offset += len(page)
    return seen


def bulk_update_by_call_id(
    updates: list[dict[str, Any]],
    supabase_url: str,
    supabase_key: str,
    chunk_size: int = 500,
    skip_missing: bool = True,
    table: str = "kkb_mastersheet",
) -> int:
    """Updates existing rows in bulk via upsert on the call_id unique
    constraint — each dict must contain call_id plus the columns to set.
    Hundreds of times faster than one PATCH per call_id.

    skip_missing (default) drops call_ids that aren't already in the table, so
    a partial update can never insert a skeleton row for a call that was never
    pushed. Returns the number of rows actually sent.
    """
    if not updates:
        print("Nothing to update.")
        return 0

    if skip_missing:
        existing = fetch_existing_call_ids(supabase_url, supabase_key, table=table)
        before = len(updates)
        updates = [u for u in updates if str(u.get("call_id")) in existing]
        if before != len(updates):
            print(f"  skipping {before - len(updates)} call_ids not present in the target table")

    url = f"{supabase_url.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    total = len(updates)
    for start in range(0, total, chunk_size):
        chunk = updates[start:start + chunk_size]
        resp = requests.post(url, headers=headers, params={"on_conflict": "call_id"}, json=chunk, timeout=120)
        print(f"  updated {start + len(chunk)}/{total} -> {resp.status_code}")
        if resp.status_code >= 400:
            print(resp.text[:1000])
            raise RuntimeError(f"Bulk update failed at rows {start + 1}-{start + len(chunk)}: {resp.text}")
    return total


def push_to_supabase(rows: list[dict[str, Any]], supabase_url: str, supabase_key: str, chunk_size: int = 500, table: str = "kkb_mastersheet") -> None:
    """Upserts rows on the call_id unique constraint, so re-pushing a batch
    updates the existing rows instead of creating duplicates."""
    if not rows:
        print("No rows to push.")
        return

    url = f"{supabase_url.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    total = len(rows)
    for start in range(0, total, chunk_size):
        chunk = rows[start:start + chunk_size]
        resp = None
        last_err = ""
        for attempt in range(5):
            try:
                resp = requests.post(url, headers=headers, params={"on_conflict": "call_id"}, json=chunk, timeout=120)
            except requests.exceptions.RequestException as exc:
                # transient DNS / dropped connection — retry rather than losing
                # an hour of fetching over a momentary network blip
                last_err = str(exc)
                print(f"  network error, retrying in {3 * (attempt + 1)}s: {last_err[:120]}")
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code in (502, 503, 504):
                last_err = resp.text[:200]
                time.sleep(3 * (attempt + 1))
                continue
            break
        if resp is None:
            raise RuntimeError(f"Supabase unreachable at rows {start + 1}-{start + len(chunk)}: {last_err}")
        print(f"Rows {start + 1}-{start + len(chunk)} of {total} -> Supabase status: {resp.status_code}")
        if resp.status_code >= 400:
            print(resp.text[:1000])
            raise RuntimeError(f"Supabase upsert failed at rows {start + 1}-{start + len(chunk)}: {resp.text}")

ROLLUP_COLUMNS = (
    "phone,phone_number,seeker_name,campaign_name,campaign_date,jfc_campaign,agent_name,"
    "intent_score,applications_count,applied_to_job,call_answered,call_engaged,drop_reasom"
)


def fetch_mastersheet_for_rollup(supabase_url: str, supabase_key: str, page_size: int = 1000) -> list[dict[str, Any]]:
    """Every kkb_mastersheet row, limited to the columns the seeker rollup
    needs (the full row includes transcripts, which would be huge)."""
    url = f"{supabase_url.rstrip('/')}/rest/v1/{table}"
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = None
        last_err = ""
        for attempt in range(4):
            try:
                resp = requests.get(
                    url,
                    headers=headers,
                    params={"select": ROLLUP_COLUMNS, "order": "id", "offset": offset, "limit": page_size},
                    timeout=120,
                )
            except requests.exceptions.RequestException as exc:
                # long reads get their connection dropped now and then
                last_err = str(exc)
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code in (502, 503, 504):
                last_err = resp.text[:200]
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Failed to read the source table: {resp.status_code} {resp.text}")
            page = resp.json()
            break
        if page is None:
            raise RuntimeError(f"Failed to read the source table at offset {offset}: {last_err}")
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    return rows


def upsert_seeker_journey(
    rows: list[dict[str, Any]],
    supabase_url: str,
    supabase_key: str,
    chunk_size: int = 500,
) -> int:
    """Upserts seeker rollup rows on the phone unique constraint, so the whole
    table can be rebuilt at any time without creating duplicates."""
    if not rows:
        print("Nothing to upsert.")
        return 0

    url = f"{supabase_url.rstrip('/')}/rest/v1/aggregated_seeker_journey"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    total = len(rows)
    for start in range(0, total, chunk_size):
        chunk = rows[start:start + chunk_size]
        resp = requests.post(url, headers=headers, params={"on_conflict": "phone"}, json=chunk, timeout=120)
        print(f"  {start + len(chunk)}/{total} -> {resp.status_code}")
        if resp.status_code >= 400:
            print(resp.text[:600])
            raise RuntimeError(f"Seeker upsert failed at rows {start + 1}-{start + len(chunk)}")
    return total

DKB_ROLLUP_COLUMNS = (
    "contact_phone,company_name,job_id,campaign_name,campaign_date,jfc_campaign,"
    "intent_score,call_status,phases_reached,update_job_status,new_job_mentioned,"
    "new_job_posted,total_jobs_posted,fields_updated,updated_vacancies,drop_reason"
)


def fetch_dkb_for_rollup(supabase_url: str, supabase_key: str, page_size: int = 1000) -> list[dict[str, Any]]:
    """Every dkb_mastersheet row, limited to the columns the provider rollup
    needs (the full row carries transcripts)."""
    url = f"{supabase_url.rstrip('/')}/rest/v1/dkb_mastersheet"
    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = None
        last_err = ""
        for attempt in range(4):
            try:
                resp = requests.get(
                    url,
                    headers=headers,
                    params={"select": DKB_ROLLUP_COLUMNS, "order": "call_id", "offset": offset, "limit": page_size},
                    timeout=120,
                )
            except requests.exceptions.RequestException as exc:
                last_err = str(exc)
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code in (502, 503, 504):
                last_err = resp.text[:200]
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Failed to read dkb_mastersheet: {resp.status_code} {resp.text}")
            page = resp.json()
            break
        if page is None:
            raise RuntimeError(f"Failed to read dkb_mastersheet at offset {offset}: {last_err}")
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    return rows


def upsert_provider_journey(
    rows: list[dict[str, Any]],
    supabase_url: str,
    supabase_key: str,
    chunk_size: int = 500,
) -> int:
    """Upserts provider rollup rows on the phone unique constraint, so the
    table can be rebuilt at any time without creating duplicates."""
    if not rows:
        print("Nothing to upsert.")
        return 0
    url = f"{supabase_url.rstrip('/')}/rest/v1/aggregated_provider_journey"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    total = len(rows)
    for start in range(0, total, chunk_size):
        chunk = rows[start:start + chunk_size]
        resp = requests.post(url, headers=headers, params={"on_conflict": "phone"}, json=chunk, timeout=120)
        print(f"  {start + len(chunk)}/{total} -> {resp.status_code}")
        if resp.status_code >= 400:
            print(resp.text[:600])
            raise RuntimeError(f"Provider upsert failed at rows {start + 1}-{start + len(chunk)}")
    return total
