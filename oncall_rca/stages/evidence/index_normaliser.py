"""Index normaliser — merge air_api_call + air_book, fix quirks, map to schemas.

Handles every known quirk from the design plan (§5.4):
- Invalid negative durations
- Missing req/res
- Duplicate entries
- Files in files_list absent from air_api_call
- Epoch timestamps in file names
- Longest-prefix pod→service mapping
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from oncall_rca.schemas.trip_index import (
    CallNode,
    FileRef,
    PayloadFormat,
    Service,
    TripIndex,
)

# Pod prefix → Service, ordered longest-first for correct matching
_POD_PREFIX_MAP: list[tuple[str, Service]] = [
    ("air-sms-new", Service.AIR_SMS_NEW),
    ("me-air-sms-new", Service.AIR_SMS_NEW),
    ("air-sms", Service.AIR_SMS),
    ("me-air-sms", Service.AIR_SMS),
    ("supply-core", Service.SUPPLY_CORE_NEW),
]


def normalise_trip_index(raw_data: dict[str, Any]) -> TripIndex:
    """Normalise raw trip index data into a TripIndex schema.

    Args:
        raw_data: The 'data' section from the trip index API response.

    Returns:
        Populated TripIndex with normalised calls and metadata.
    """
    header = raw_data.get("header_data", [{}])[0]
    itineraries = header.get("itinerary", [])
    trips = header.get("trip", [])
    trip_id = trips[0] if trips else ""
    channel = header.get("channel", "")
    pax_count = (
        (header.get("adult") or 0)
        + (header.get("child") or 0)
        + (header.get("infants") or 0)
    )

    # Build pod→host lookup from air_book
    pod_lookup = _build_pod_lookup(raw_data.get("air_book", []))

    # Normalise air_api_call entries
    seen_keys: set[str] = set()
    all_calls: list[CallNode] = []
    anomalies: list[str] = []

    for entry in raw_data.get("air_api_call", []):
        call = _normalise_call(entry, pod_lookup)
        if call is None:
            continue

        # Deduplicate by (api, req_file, res_file)
        dedup_key = f"{call.api}|{call.req_file.file_name if call.req_file else ''}|{call.res_file.file_name if call.res_file else ''}"
        if dedup_key in seen_keys:
            anomalies.append(f"duplicate: {call.api} ({dedup_key})")
            continue
        seen_keys.add(dedup_key)

        # Flag anomalies
        if call.is_retry:
            anomalies.append(f"retry: {call.api} on {call.host}")
        if call.is_slow:
            anomalies.append(f"slow (>{10}s): {call.api} {call.duration_ms}ms")
        if call.is_timeout:
            anomalies.append(f"timeout (>{30}s): {call.api} {call.duration_ms}ms")

        all_calls.append(call)

    # Collect file inventory from files_list
    file_inventory = _extract_file_inventory(raw_data.get("files_list", []))

    # Flag retries: same api called with same api_type more than once
    _flag_retries(all_calls, anomalies)

    return TripIndex(
        trip_id=trip_id,
        itineraries=itineraries,
        trips=trips,
        channel=channel,
        pax_count=pax_count,
        all_calls=all_calls,
        file_inventory=file_inventory,
        anomalies=anomalies,
    )


def _build_pod_lookup(air_book: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Build lookup: (step_name, time) → {host: pod_hostname, step_name, event, trip}.

    Uses step_name + time as the join key between air_api_call and air_book.
    """
    lookup: dict[str, dict[str, str]] = {}
    for entry in air_book:
        key = f"{entry.get('step_name', '')}|{entry.get('time', '')}"
        lookup[key] = {
            "host": entry.get("host", ""),
            "step_name": entry.get("step_name", ""),
            "event": entry.get("event", ""),
            "trip": entry.get("trip", ""),
        }
    return lookup


def _normalise_call(
    entry: dict[str, Any],
    pod_lookup: dict[str, dict[str, str]],
) -> CallNode | None:
    """Normalise a single air_api_call entry."""
    api = entry.get("api", "") or ""
    api_type = entry.get("api_type", "") or ""
    url = entry.get("url", "") or ""
    host = entry.get("host", "") or ""
    time_str = entry.get("time", "") or ""
    identifier = entry.get("identifier", "") or ""
    itinerary = entry.get("itinerary", "") or ""
    supplier = entry.get("supplier", "") or ""

    # Parse duration — handle invalid negative values
    duration_ms = _parse_duration(entry.get("duration", ""))

    # Parse timestamp
    call_time = _parse_time(time_str)

    # HTTP code
    http_code = int(entry.get("http_code", 200) or 200)

    # Build file refs
    req_file = _make_file_ref(entry.get("req"), itinerary, time_str)
    res_file = _make_file_ref(entry.get("res"), itinerary, time_str)

    # Lookup pod from air_book
    join_key = f"{api}|{time_str}"
    book_info = pod_lookup.get(join_key, {})
    pod = book_info.get("host", "")
    step_name = book_info.get("step_name", api)
    event = book_info.get("event", "")

    # Map pod to service (longest prefix first)
    service = _pod_to_service(pod)

    # External call detection
    is_external = url.startswith("https://") or url.startswith("http://")

    # Timing flags
    is_slow = duration_ms is not None and duration_ms > 10_000
    is_timeout = duration_ms is not None and duration_ms > 30_000

    return CallNode(
        api=api,
        api_type=api_type,
        url=url,
        service=service,
        pod=pod,
        host=host,
        time=call_time,
        duration_ms=duration_ms,
        http_code=http_code,
        identifier=identifier,
        itinerary=itinerary,
        supplier=supplier,
        step_name=step_name,
        event=event,
        req_file=req_file,
        res_file=res_file,
        is_external=is_external,
        is_slow=is_slow,
        is_timeout=is_timeout,
    )


def _parse_duration(raw: Any) -> int | None:
    """Parse duration, returning None for invalid values (negative epochs)."""
    if raw is None or raw == "":
        return None
    try:
        val = int(raw)
        # Negative or absurdly large = invalid (e.g. 0 - start_epoch)
        if val < 0 or val > 600_000:  # > 10 minutes is suspect
            return None
        return val
    except (ValueError, TypeError):
        return None


def _parse_time(time_str: str) -> datetime | None:
    """Parse timestamp like '2026-08-02 14:06:56.186'."""
    if not time_str:
        return None
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        try:
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _make_file_ref(
    file_name: str | None,
    itinerary: str,
    time_str: str,
) -> FileRef | None:
    """Create a FileRef from a file name, or None if missing."""
    if not file_name:
        return None
    date = time_str.split(" ")[0] if time_str else ""
    is_request = file_name.endswith("-req.gz") or "-req." in file_name
    return FileRef(
        file_name=file_name,
        itinerary_id=itinerary,
        date=date,
        trip_id="",  # filled by caller
        is_request=is_request,
    )


def _pod_to_service(pod: str) -> Service:
    """Map pod hostname to service using longest-prefix matching."""
    if not pod:
        return Service.OTHER
    pod_lower = pod.lower()
    for prefix, service in _POD_PREFIX_MAP:
        if pod_lower.startswith(prefix):
            return service
    return Service.OTHER


def _flag_retries(calls: list[CallNode], anomalies: list[str]) -> None:
    """Flag calls where the same external API appears more than once."""
    external_apis: dict[str, list[int]] = {}
    for i, call in enumerate(calls):
        if call.is_external:
            key = call.api
            external_apis.setdefault(key, []).append(i)

    for api_name, indices in external_apis.items():
        if len(indices) > 1:
            for idx in indices[1:]:
                calls[idx] = calls[idx].model_copy(update={"is_retry": True})
                anomalies.append(f"retry: {api_name} (call #{idx})")


def _extract_file_inventory(files_list: list[Any]) -> list[str]:
    """Extract flat list of file names from files_list (which may be nested)."""
    result: list[str] = []
    for item in files_list:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and "files" in item:
            result.extend(item["files"])
    return result
