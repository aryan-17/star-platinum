"""Call tree builder, journey splitter, and supplier detector.

Journey attribution uses (§8.5):
1. SMS_BOOK URL route encoding (e.g. AIR_ARABIA__COK__CAI__3L__12)
2. Host chaining — calls from the same host belong to the same journey
3. Correlation identifiers linking parent/child calls
"""

from __future__ import annotations

import re
from collections import defaultdict

from oncall_rca.schemas.trip_index import CallNode, Journey, TripIndex

# Supplier detection by external call URL domain
_SUPPLIER_DOMAIN_MAP: dict[str, str] = {
    "airarabia.com": "air_arabia",
    "accelaero.com": "air_arabia",
    "amadeus.com": "amadeus",
    "sabre.com": "sabre",
    "flydubai.com": "flydubai",
}

# Route pattern in SMS_BOOK URLs: /journey/book/SUPPLIER__ORIGIN__DEST__CARRIER__*
_ROUTE_PATTERN = re.compile(
    r"/(?:journey/book|v2/journey/book)/([A-Z_]+)__([A-Z]{3})__([A-Z]{3})__([A-Z0-9]{2})__"
)


def build_call_tree(trip_index: TripIndex) -> TripIndex:
    """Build parent-child call tree from identifier field and link children.

    Returns updated TripIndex with children populated on parent CallNodes.
    """
    calls = trip_index.all_calls

    # Group by identifier
    by_identifier: dict[str, list[int]] = defaultdict(list)
    for i, call in enumerate(calls):
        if call.identifier:
            by_identifier[call.identifier].append(i)

    # For groups with >1 call, first call is parent, rest are children
    # (heuristic: sorted by time)
    updated_calls = [c.model_copy() for c in calls]
    for identifier, indices in by_identifier.items():
        if len(indices) <= 1:
            continue
        # Sort by time
        indices.sort(key=lambda i: calls[i].time or calls[0].time)
        parent_idx = indices[0]
        children = [updated_calls[i] for i in indices[1:]]
        updated_calls[parent_idx] = updated_calls[parent_idx].model_copy(
            update={"children": children}
        )

    return trip_index.model_copy(update={"all_calls": updated_calls})


def split_journeys(trip_index: TripIndex) -> TripIndex:
    """Split calls into per-journey groups using route info and host chaining.

    Strategy:
    1. Find SMS_BOOK calls — their URLs encode origin/dest/carrier.
    2. Each SMS_BOOK host defines a journey's host chain.
    3. Attribute all NEW-SMS calls by matching host to a journey's chain.
    4. Remaining calls go unattributed (journey_index stays default).
    """
    calls = trip_index.all_calls
    journeys: list[Journey] = []
    journey_calls: dict[int, list[CallNode]] = defaultdict(list)

    # Step 1: Find SMS_BOOK calls and extract routes
    sms_book_routes: list[dict] = []
    for call in calls:
        if call.api != "SMS_BOOK":
            continue
        route = _extract_route(call.url)
        if route:
            sms_book_routes.append({
                "host": call.host,
                "origin": route["origin"],
                "destination": route["destination"],
                "carrier": route["carrier"],
                "supplier_raw": route["supplier_raw"],
            })

    # Step 2: Build host → journey_index mapping
    host_to_journey: dict[str, int] = {}
    for idx, route in enumerate(sms_book_routes):
        journeys.append(Journey(
            journey_index=idx,
            origin=route["origin"],
            destination=route["destination"],
            supplier=_detect_supplier_from_calls(calls, route["host"]),
            supplier_detected_by=f"url:sms_book_route:{route['origin']}→{route['destination']}",
        ))
        host_to_journey[route["host"]] = idx

    # Also map SMS_HOLD hosts to journeys by matching time windows
    _map_hold_hosts(calls, host_to_journey)

    # Step 3: Attribute calls to journeys by host
    for call in calls:
        if call.host in host_to_journey:
            j_idx = host_to_journey[call.host]
            journey_calls[j_idx].append(call)

    # Populate journey calls
    for j in journeys:
        j_calls = journey_calls.get(j.journey_index, [])
        j = j.model_copy(update={"calls": j_calls})
        journeys[j.journey_index] = j

    # If no SMS_BOOK found, create a single default journey
    if not journeys:
        supplier = _detect_supplier_from_domain(calls)
        journeys.append(Journey(
            journey_index=0,
            supplier=supplier,
            supplier_detected_by="url:external_domain" if supplier else "",
            calls=calls,
        ))

    return trip_index.model_copy(update={"journeys": journeys})


def detect_supplier(trip_index: TripIndex) -> TripIndex:
    """Detect supplier from external call URLs and update journeys."""
    journeys = trip_index.journeys
    if not journeys:
        return trip_index

    updated = []
    for j in journeys:
        if not j.supplier:
            supplier = _detect_supplier_from_domain(j.calls or trip_index.all_calls)
            j = j.model_copy(update={
                "supplier": supplier,
                "supplier_detected_by": f"url:external_domain:{supplier}" if supplier else "",
            })
        updated.append(j)

    return trip_index.model_copy(update={"journeys": updated})


def _extract_route(url: str) -> dict[str, str] | None:
    """Extract route info from SMS_BOOK URL."""
    match = _ROUTE_PATTERN.search(url)
    if not match:
        return None
    return {
        "supplier_raw": match.group(1),
        "origin": match.group(2),
        "destination": match.group(3),
        "carrier": match.group(4),
    }


def _detect_supplier_from_calls(calls: list[CallNode], host: str) -> str:
    """Detect supplier from external calls made by a specific host."""
    for call in calls:
        if call.host == host and call.is_external:
            return _domain_to_supplier(call.url)
    return ""


def _detect_supplier_from_domain(calls: list[CallNode]) -> str:
    """Detect supplier from any external call URL."""
    for call in calls:
        if call.is_external:
            supplier = _domain_to_supplier(call.url)
            if supplier:
                return supplier
    return ""


def _domain_to_supplier(url: str) -> str:
    """Map URL domain to supplier name."""
    for domain, supplier in _SUPPLIER_DOMAIN_MAP.items():
        if domain in url:
            return supplier
    return ""


def _map_hold_hosts(calls: list[CallNode], host_to_journey: dict[str, int]) -> None:
    """Map SMS_HOLD hosts to journeys by matching their child external calls' hosts."""
    # External calls (SUPPLIER_*) share hosts with their parent SMS_HOLD/SMS_BOOK
    # The host_to_journey already has SMS_BOOK hosts. Add SUPPLIER_* hosts from
    # same-host chains.
    known_hosts = set(host_to_journey.keys())
    for call in calls:
        if call.host in known_hosts:
            continue
        # For SMS_HOLD and HOLD_CORE, try to find a SUPPLIER_* call on the same host
        # that we already know the journey for
        if call.api in ("SMS_HOLD", "HOLD_CORE"):
            # Check if any call from the same identifier group has a known host
            for other in calls:
                if other.identifier == call.identifier and other.host in known_hosts:
                    host_to_journey[call.host] = host_to_journey[other.host]
                    known_hosts.add(call.host)
                    break
