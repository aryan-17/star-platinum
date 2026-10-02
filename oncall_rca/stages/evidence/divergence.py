"""Divergence finder — trace an invariant through the baggage chain and find where it breaks.

The playbook defines the chain of steps and what to compare at each step.
This module walks the chain and returns the first divergence point, or None.
"""

from __future__ import annotations

from oncall_rca.schemas.evidence import (
    BaggageChainStep,
    DivergencePoint,
    Citation,
    EvidenceFact,
)


# Divergence class descriptions from §8.4
_DIVERGENCE_CLASSES: dict[BaggageChainStep, str] = {
    BaggageChainStep.SS1_CONSISTENCY: "SS1 responses inconsistent across calls",
    BaggageChainStep.SIS_HOLD_REQUEST: "SIS-HOLD FBC/fare != SS1",
    BaggageChainStep.SUPPLIER_BAGGAGE_HOLD: "Supplier free tier != SS1 benefit",
    BaggageChainStep.SUPPLIER_PRICE_HOLD: "Supplier returned different fare during HOLD",
    BaggageChainStep.HOLD_CORE_RESPONSE: "HOLD_CORE fare != SIS-HOLD request",
    BaggageChainStep.BOOK_RESPONSE: "Booking failed or baggage missing in book",
    BaggageChainStep.SUPPLIER_BOOK: "SUPPLIER_BOOK baggage mismatch or booking failed",
    BaggageChainStep.SUPPLIER_BOOK_RETRY: "Retry changed fare/baggage",
}


def find_first_divergence(
    facts: list[EvidenceFact],
    journey_index: int,
) -> DivergencePoint | None:
    """Find the first step where baggage/fare diverges for a journey.

    Walks the baggage chain in order. For each step, checks if the value
    matches the expected baseline (SS1 displayed value).

    Args:
        facts: All extracted facts for this journey.
        journey_index: Which journey to check.

    Returns:
        DivergencePoint if found, None if all steps are consistent.
    """
    journey_facts = [f for f in facts if f.journey_index == journey_index]
    if not journey_facts:
        return None

    # Get baseline: what SS1 displayed
    ss1_facts = [f for f in journey_facts if f.step == BaggageChainStep.SS1_DISPLAYED]
    if not ss1_facts:
        return None  # can't check without baseline

    # Group facts by step for comparison
    facts_by_step: dict[BaggageChainStep, list[EvidenceFact]] = {}
    for fact in journey_facts:
        facts_by_step.setdefault(fact.step, []).append(fact)

    # Check SS1 consistency first
    ss1_consistency = facts_by_step.get(BaggageChainStep.SS1_CONSISTENCY, [])
    for fact in ss1_consistency:
        if "inconsistent" in fact.value.lower() or "mismatch" in fact.value.lower():
            return DivergencePoint(
                journey_index=journey_index,
                step=BaggageChainStep.SS1_CONSISTENCY,
                expected_value="consistent across all SS1 calls",
                actual_value=fact.value,
                citation=fact.citation,
                divergence_class=_DIVERGENCE_CLASSES[BaggageChainStep.SS1_CONSISTENCY],
            )

    # Walk the chain looking for status/booking failures
    chain_order = [
        BaggageChainStep.SIS_HOLD_REQUEST,
        BaggageChainStep.SUPPLIER_BAGGAGE_HOLD,
        BaggageChainStep.SUPPLIER_PRICE_HOLD,
        BaggageChainStep.HOLD_CORE_RESPONSE,
        BaggageChainStep.BOOK_RESPONSE,
        BaggageChainStep.SUPPLIER_BOOK,
        BaggageChainStep.SUPPLIER_BOOK_RETRY,
    ]

    for step in chain_order:
        step_facts = facts_by_step.get(step, [])
        for fact in step_facts:
            divergence = _check_fact_for_divergence(fact, ss1_facts, step)
            if divergence:
                return divergence

    return None


def _check_fact_for_divergence(
    fact: EvidenceFact,
    ss1_facts: list[EvidenceFact],
    step: BaggageChainStep,
) -> DivergencePoint | None:
    """Check a single fact for divergence indicators."""

    # Check for explicit failure states
    if "error" in fact.label.lower():
        return DivergencePoint(
            journey_index=fact.journey_index,
            step=step,
            expected_value="success",
            actual_value=fact.value,
            citation=fact.citation,
            divergence_class=_DIVERGENCE_CLASSES.get(step, ""),
        )

    # Check hold status
    if "hold status" in fact.label.lower():
        if fact.value not in ("HOLD_SUCCESS", "SUCCESS", "true"):
            return DivergencePoint(
                journey_index=fact.journey_index,
                step=step,
                expected_value="HOLD_SUCCESS",
                actual_value=fact.value,
                citation=fact.citation,
                divergence_class=_DIVERGENCE_CLASSES.get(step, ""),
            )

    # Check booking status
    if "booking status" in fact.label.lower():
        if fact.value not in ("CNF", "CONFIRMED", "SUCCESS"):
            return DivergencePoint(
                journey_index=fact.journey_index,
                step=step,
                expected_value="CNF",
                actual_value=fact.value,
                citation=fact.citation,
                divergence_class=_DIVERGENCE_CLASSES.get(step, ""),
            )

    return None
