"""Evidence Engine — orchestrates extraction and divergence finding.

Ties together: normaliser → call tree → journey split → extractors → divergence.
No LLM in this path — all deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oncall_rca.schemas.evidence import EvidenceFact, EvidencePack
from oncall_rca.schemas.trip_index import TripIndex
from oncall_rca.stages.evidence.call_tree import build_call_tree, detect_supplier, split_journeys
from oncall_rca.stages.evidence.divergence import find_first_divergence
from oncall_rca.stages.evidence.extractors.base import get_extractor
from oncall_rca.stages.evidence.index_normaliser import normalise_trip_index
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import detect_format, parse_payload

# Import extractors to register them
import oncall_rca.stages.evidence.extractors.hold_core  # noqa: F401
import oncall_rca.stages.evidence.extractors.supplier_book  # noqa: F401
import oncall_rca.stages.evidence.extractors.book  # noqa: F401

# Map API names to baggage chain steps for extraction
from oncall_rca.schemas.evidence import BaggageChainStep

_API_TO_STEP: dict[str, BaggageChainStep] = {
    "HOLD_CORE": BaggageChainStep.HOLD_CORE_RESPONSE,
    "SUPPLIER_BOOK": BaggageChainStep.SUPPLIER_BOOK,
    "BOOK": BaggageChainStep.BOOK_RESPONSE,
}

# Known red herrings
_RED_HERRINGS = [
    "journeyFareSummary.passengerBaggageDetails is fare-level metadata, not booked baggage",
    "SUPPLIER_BOOK session-expiry retry (err.2-maxico) is expected for long hold-to-book gaps",
]


def build_evidence_pack(
    trip_id: str,
    raw_data: dict[str, Any],
    cache: TripCache,
) -> EvidencePack:
    """Build a complete EvidencePack for a trip.

    Args:
        trip_id: Trip reference.
        raw_data: Raw trip index data (the 'data' section).
        cache: File cache for fetching payloads.

    Returns:
        EvidencePack with facts, divergence point, and anomalies.
    """
    # Phase 1: Normalise and structure
    trip_index = normalise_trip_index(raw_data)
    trip_index = build_call_tree(trip_index)
    trip_index = split_journeys(trip_index)
    trip_index = detect_supplier(trip_index)

    # Phase 2: Extract facts from cached payload files
    all_facts: list[EvidenceFact] = []
    for journey in trip_index.journeys:
        journey_facts = _extract_journey_facts(
            journey.journey_index,
            journey.calls or [],
            trip_id,
            cache,
        )
        all_facts.extend(journey_facts)

    # Phase 3: Find divergence per journey
    divergence_point = None
    for journey in trip_index.journeys:
        dp = find_first_divergence(all_facts, journey.journey_index)
        if dp is not None:
            divergence_point = dp
            break  # report first divergence found

    # Build facts_by_journey
    facts_by_journey: dict[int, list[EvidenceFact]] = {}
    for fact in all_facts:
        facts_by_journey.setdefault(fact.journey_index, []).append(fact)

    return EvidencePack(
        trip_index=trip_index,
        facts=all_facts,
        divergence_point=divergence_point,
        anomalies=trip_index.anomalies,
        red_herrings=_RED_HERRINGS,
        supplier=trip_index.journeys[0].supplier if trip_index.journeys else "",
        facts_by_journey=facts_by_journey,
    )


def _extract_journey_facts(
    journey_index: int,
    calls: list,
    trip_id: str,
    cache: TripCache,
) -> list[EvidenceFact]:
    """Extract facts from payload files for calls in a journey."""
    facts: list[EvidenceFact] = []

    for call in calls:
        step = _API_TO_STEP.get(call.api)
        if step is None:
            continue

        extractor = get_extractor(step)
        if extractor is None:
            continue

        # Try to read response file from cache
        if call.res_file and cache.has_file(trip_id, call.res_file.file_name):
            try:
                content_bytes = cache.read_file(trip_id, call.res_file.file_name)
                parsed = parse_payload(content_bytes)
                extracted = extractor.extract(
                    parsed,
                    journey_index=journey_index,
                    source_file=call.res_file.file_name,
                )
                facts.extend(extracted)
            except Exception:
                pass  # skip unparseable files

    return facts
