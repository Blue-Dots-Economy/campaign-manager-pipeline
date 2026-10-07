"""Thin client for the Raya API - fetching only, no transformation.

A standalone COPY, deliberately not imported from ../campaign_manager_pipeline.
The two pipelines talk to different Supabase projects, and a shared import is
the kind of link that later turns into one script loading the wrong .env and
writing to the wrong database. A few hundred duplicated lines is the cheaper
mistake.
"""
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

RAYA_BASE_URL = "https://v1.getraya.app"

# Shared session with a big enough connection pool that concurrent transcript
# fetches actually run concurrently instead of
# queuing for a connection — the default adapter pool is only 10.
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def _get_with_retry(url: str, max_retries: int = 6, **kwargs: Any) -> requests.Response:
    """GET with automatic backoff on 429 and on transient server errors.

    429 is needed because concurrent fetches (see build_rows_for_batches) trip
    Raya's rate limiter. 5xx matters just as much: Raya sits behind Cloudflare
    and intermittently answers 502/503/504/520/522 with an HTML error page. A
    full push walks tens of thousands of contacts over twenty-odd minutes, so
    passing one of those straight back to the caller — which then raises — threw
    away the whole run for a hiccup that a one-second retry fixes.
    """
    RETRY_STATUS = (429, 500, 502, 503, 504, 520, 522, 524)
    resp = None
    for attempt in range(max_retries + 1):
        try:
            resp = _session.get(url, **kwargs)
        except requests.RequestException:
            # a dropped connection is the same class of problem as a 502
            if attempt == max_retries:
                raise
            time.sleep(min(2 ** attempt, 15))
            continue
        if resp.status_code not in RETRY_STATUS:
            return resp
        if attempt == max_retries:
            return resp
        retry_after = 1.0
        if resp.status_code == 429:
            try:
                retry_after = float(resp.json().get("retry_after", 1))
            except Exception:
                pass
        else:
            retry_after = min(2 ** attempt, 15)   # plain backoff for 5xx
        time.sleep(max(retry_after, 0.5) * (1 if resp.status_code != 429 else attempt + 1))
    return resp


def raya_headers(api_key: str) -> dict[str, str]:
    return {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }


def fetch_agents(api_key: str, offset: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
    resp = _get_with_retry(
        f"{RAYA_BASE_URL}/api/agent",
        headers=raya_headers(api_key),
        params={"offset": offset, "limit": limit},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch agents: {resp.status_code} {resp.text}")
    payload = resp.json()
    return payload.get("agents", []), payload.get("total", 0)


def fetch_all_agents(api_key: str) -> list[dict[str, Any]]:
    all_agents: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    while True:
        agents, total = fetch_agents(api_key, offset=offset, limit=limit)
        if not agents:
            break
        all_agents.extend(agents)
        if len(agents) < limit:
            break
        offset += len(agents)
        if total and offset >= total:
            break
    return all_agents


def fetch_batches(api_key: str, agent_id: str, offset: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
    resp = _get_with_retry(
        f"{RAYA_BASE_URL}/api/batch",
        headers=raya_headers(api_key),
        params={"agent_id": agent_id, "offset": offset, "limit": limit, "sort": "desc"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch batches: {resp.status_code} {resp.text}")
    payload = resp.json()
    return payload.get("batches", []), payload.get("total", 0)


def fetch_contacts(api_key: str, batch_id: int | str, offset: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
    resp = _get_with_retry(
        f"{RAYA_BASE_URL}/api/batch/{batch_id}/contacts",
        headers=raya_headers(api_key),
        params={"offset": offset, "limit": limit},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch contacts for batch {batch_id}: {resp.status_code} {resp.text}")
    payload = resp.json()
    return payload.get("contacts", []), payload.get("total", 0)


def fetch_calls(api_key: str, agent_id: str, offset: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
    """Every call for an agent, batch or no batch.

    The only way to see inbound calls. /api/batch/{id}/contacts can't show
    them: an inbound call has no contact record and belongs to no batch, so
    walking batches — which is what the whole outbound pipeline does — misses
    them entirely.
    """
    resp = _get_with_retry(
        f"{RAYA_BASE_URL}/api/call",
        headers=raya_headers(api_key),
        params={"agent_id": agent_id, "offset": offset, "limit": limit},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch calls for {agent_id}: {resp.status_code} {resp.text}")
    payload = resp.json()
    return payload.get("calls", []), payload.get("total", 0)


def fetch_all_calls(api_key: str, agent_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset, limit = 0, 100
    while True:
        calls, total = fetch_calls(api_key, agent_id, offset=offset, limit=limit)
        if not calls:
            break
        out.extend(calls)
        if len(calls) < limit:
            break
        offset += len(calls)
        if total and offset >= total:
            break
    return out


def is_inbound(call: dict[str, Any]) -> bool:
    """Raya records no direction, so it has to be inferred.

    An inbound call has the seeker's number in caller_no and nothing in
    to_number; an outbound call is the other way round. Checked against the
    batch-contact population, where every call is outbound by construction.
    """
    return bool(call.get("caller_no")) and not call.get("to_number")


def fetch_all_batches(api_key: str, agent_id: str) -> list[dict[str, Any]]:
    all_batches: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    while True:
        batches, total = fetch_batches(api_key, agent_id, offset=offset, limit=limit)
        if not batches:
            break
        all_batches.extend(batches)
        if len(batches) < limit:
            break
        offset += len(batches)
        if total and offset >= total:
            break
    return all_batches


def fetch_call_detail(api_key: str, call_uuid: str) -> dict[str, Any] | None:
    """Full per-call record. /api/batch/{id}/contacts returns only 8 fields per
    call — no transcript and no recording URL — so anything beyond duration and
    outcome has to come from this per-call endpoint."""
    if not call_uuid:
        return None
    resp = _get_with_retry(
        f"{RAYA_BASE_URL}/api/call/{call_uuid}",
        headers=raya_headers(api_key),
        timeout=30,
    )
    if resp.status_code != 200:
        return None
    payload = resp.json()
    return payload if isinstance(payload, dict) else None


def fetch_all_contacts(api_key: str, batch_id: int | str) -> list[dict[str, Any]]:
    all_contacts: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    while True:
        contacts, total = fetch_contacts(api_key, batch_id, offset=offset, limit=limit)
        if not contacts:
            break
        all_contacts.extend(contacts)
        if len(contacts) < limit:
            break
        offset += len(contacts)
        if total and offset >= total:
            break
    return all_contacts
