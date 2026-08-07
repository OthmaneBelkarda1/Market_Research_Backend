"""
Apify extraction backend
========================

Runs a hosted actor for one product URL and maps its dataset record into the
flat field language.

Actors are started through the **REST API**, not the Apify MCP server: the MCP
server tags runs with origin='MCP', which actors built on an older Apify SDK
reject outright (pydantic ValidationError on meta.origin, exit code 91). REST
runs default to origin='API', which every SDK version accepts.

Flow:  POST /acts/{id}/runs  ->  poll /actor-runs/{runId}  ->  GET dataset items
"""

import asyncio
import json
from typing import Any

import httpx

from .actors import ActorAdapter, get_adapter, _best_match
from .config import (
    ACTOR_POLL_INTERVAL_S,
    ACTOR_START_TIMEOUT_S,
    APIFY_BASE_URL,
    MAX_RAW_RECORD_CHARS,
    ActorRunError,
    require_apify_token,
)
from .sources import SourceResult

# A run is finished once it reaches one of these; anything else (READY,
# RUNNING, ABORTING) means keep waiting — freshly started runs sit in READY.
_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"})


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {require_apify_token()}"}


async def run_actor(actor_id: str, run_input: dict[str, Any], *,
                    timeout_s: int = ACTOR_START_TIMEOUT_S) -> list[dict]:
    """Start an actor, wait for it to finish, and return its dataset items."""
    actor_path = actor_id.replace("/", "~")
    headers = _auth_headers()

    try:
        return await _run_actor(actor_path, actor_id, run_input, headers, timeout_s)
    except httpx.HTTPError as exc:
        # DNS/TLS/timeouts must surface as our own error type: callers (and the
        # agent's tool loop) handle ExtractionError, not raw transport faults.
        raise ActorRunError(
            f"Could not reach the Apify API for {actor_id}: {type(exc).__name__}: {exc}"
        ) from exc


async def _run_actor(actor_path: str, actor_id: str, run_input: dict[str, Any],
                     headers: dict[str, str], timeout_s: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=60, headers=headers) as client:
        start = await client.post(
            f"{APIFY_BASE_URL}/acts/{actor_path}/runs",
            json=run_input,
            params={"timeout": timeout_s},
        )
        if start.status_code >= 400:
            raise ActorRunError(
                f"Could not start actor {actor_id}: HTTP {start.status_code} {start.text[:300]}"
            )
        run = start.json()["data"]
        run_id, dataset_id, status = run["id"], run["defaultDatasetId"], run["status"]

        deadline = timeout_s / ACTOR_POLL_INTERVAL_S
        polls = 0
        while status not in _TERMINAL_STATUSES and polls < deadline:
            await asyncio.sleep(ACTOR_POLL_INTERVAL_S)
            polls += 1
            poll = await client.get(f"{APIFY_BASE_URL}/actor-runs/{run_id}")
            if poll.status_code < 400:
                status = poll.json()["data"]["status"]

        if status != "SUCCEEDED":
            raise ActorRunError(
                f"Actor {actor_id} run {run_id} ended with status={status}. "
                f"Logs: https://console.apify.com/actors/runs/{run_id}"
            )

        items = await client.get(
            f"{APIFY_BASE_URL}/datasets/{dataset_id}/items",
            params={"clean": "true", "format": "json"},
        )
        items.raise_for_status()
        data = items.json()
        return data if isinstance(data, list) else [data]


def _select(adapter: ActorAdapter, items: list[dict], url: str) -> dict:
    """Pick the record for our URL, ignoring the summary rows some actors add."""
    products = [
        item for item in items
        if isinstance(item, dict) and not any(
            str(key).upper() in {"SUMMARY", "TOP_PRODUCTS", "STATS"} for key in item
        )
    ] or [i for i in items if isinstance(i, dict)]
    chooser = adapter.select_record or _best_match
    record = chooser(products, url)
    if not record:
        raise ActorRunError(
            f"Actor {adapter.actor_id} returned no product for {url}. "
            "The URL may be invalid, region-locked, or the product delisted."
        )
    return record


async def extract_with_apify(url: str, actor_key: str) -> SourceResult:
    """Scrape one product URL through the actor registered under `actor_key`."""
    adapter = get_adapter(actor_key)
    run_input = {**adapter.build_input(url), **adapter.extra_input}

    items = await run_actor(adapter.actor_id, run_input)
    record = _select(adapter, items, url)

    warnings: list[str] = []
    try:
        # Actors that spread one product over several rows aggregate the whole
        # dataset; the rest map the single selected record.
        fields = (adapter.aggregate(items, url) if adapter.aggregate
                  else adapter.map_record(record))
    except Exception as exc:                      # noqa: BLE001 - resilience by design
        # A broken mapper must not lose the run: the raw record still goes to
        # the LLM, which can read it directly.
        fields = {}
        warnings.append(f"{adapter.actor_id} mapper failed: {type(exc).__name__}: {exc}")

    # Actors answer 200-with-nothing when a page is anti-bot blocked or the
    # product is gone. Fail loudly so the caller (or the agent) can try another
    # route instead of returning an all-null record.
    if not fields.get("title") and not fields.get("price_amount"):
        detail = record.get("_warnings") or ""
        raise ActorRunError(
            f"Actor {adapter.actor_id} returned an empty record for {url}"
            f"{f' ({detail})' if detail else ''} — the product may be delisted, "
            "region-locked, or the actor was blocked."
        )

    return SourceResult(
        strategy="apify",
        source=f"apify:{adapter.actor_id}",
        url=url,
        final_url=str(record.get("url") or record.get("Product URL") or url),
        fields=fields,
        context=json.dumps(items[:5] if adapter.aggregate else record,
                           ensure_ascii=False, default=str)[:MAX_RAW_RECORD_CHARS],
        raw=record,
        warnings=warnings,
    )
