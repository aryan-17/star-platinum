"""Investigator — Groq tool-calling loop to explain WHY the divergence happened.

Receives EvidencePack + CodeContext, forms competing hypotheses,
proves/disproves each with tools. Hard limits on iterations/tokens/time.
"""

from __future__ import annotations

import time

from oncall_rca.llm.client import LLMBudgetExceeded, LLMClient
from oncall_rca.schemas.code_context import CodeContext
from oncall_rca.schemas.evidence import EvidencePack
from oncall_rca.schemas.hypothesis import Hypothesis

_SYSTEM_PROMPT = """You are an expert flight booking systems investigator.

You are given evidence about a baggage mismatch incident and relevant code.
Your job is to explain WHY the divergence happened — not just where.

## Two-worlds principle
supply-core = what the user was shown (displayed world)
air-sms = what was actually booked (booked world)
Compare them. If they disagree, either supply-core built the request wrongly
(our bug) or the supplier returned something different (supplier-side).

## Fare comparison — START HERE
Your root cause MUST begin by comparing the fare the user asked to book vs the fare
actually booked, for EACH journey. Trace the fare through the chain:
1. SS1 (displayed): which fare family / comboFbc / baggageFareType did the user see?
2. SIS-HOLD: which fareId / comboFbc was sent to the supplier for hold?
3. SMS_SUPPLIER_HOLD: which fareId came back? What baggage did it return?
4. BOOK: which fare was actually booked?
If the fareId or comboFbc changes between steps, THAT is the divergence. Name the
exact step where the fare switched and explain why.

## Rules
- Every claim MUST cite evidence: (log file + field path) or (code file + line)
- Form competing hypotheses and rule them out with evidence
- If you cannot determine the root cause with high confidence, say so
- Do NOT blame the session-expiry retry (err.2-maxico) — that is expected behaviour
- Do NOT use journeyFareSummary.passengerBaggageDetails — it is fare metadata, not booked baggage
- INVESTIGATE EVERY JOURNEY. A trip may have multiple journeys (e.g. outbound + return).
  Analyse each journey independently — the bug may affect only one leg.

## Output
Respond with a JSON object matching the Hypothesis schema."""


def investigate(
    evidence: EvidencePack,
    code_context: CodeContext,
    llm: LLMClient,
    *,
    max_iterations: int = 10,
    wall_clock_limit: int = 300,
) -> Hypothesis:
    """Run the Investigator for the whole trip (legacy single-hypothesis).

    Args:
        evidence: Complete evidence pack with facts and divergence point.
        code_context: Relevant code files.
        llm: LLM client for reasoning.
        max_iterations: Max tool-calling rounds.
        wall_clock_limit: Max wall-clock seconds.

    Returns:
        Hypothesis with root cause explanation and citations.
    """
    prompt = _build_prompt(evidence, code_context)
    return _run_llm(prompt, llm, wall_clock_limit)


def investigate_journey(
    evidence: EvidencePack,
    journey_index: int,
    code_context: CodeContext,
    llm: LLMClient,
    *,
    wall_clock_limit: int = 300,
) -> Hypothesis:
    """Run the Investigator for a single journey.

    Builds a focused prompt with only this journey's evidence and fare chain.
    """
    prompt = _build_journey_prompt(evidence, journey_index, code_context)
    return _run_llm(prompt, llm, wall_clock_limit)


def _run_llm(prompt: str, llm: LLMClient, wall_clock_limit: int) -> Hypothesis:
    """Common LLM call logic."""
    start = time.monotonic()
    try:
        hypothesis = llm.generate(
            prompt=prompt,
            system=_SYSTEM_PROMPT,
            response_schema=Hypothesis,
            stage="investigator",
            temperature=0.0,
            max_tokens=4096,
        )
    except LLMBudgetExceeded:
        return Hypothesis(
            claim="Investigation incomplete — token budget exceeded",
            explanation="The investigator ran out of token budget before reaching a conclusion.",
        )

    elapsed = time.monotonic() - start
    if elapsed > wall_clock_limit:
        hypothesis = hypothesis.model_copy(update={
            "claim": f"{hypothesis.claim} (wall-clock limit reached)",
        })

    return hypothesis


def _build_journey_prompt(
    evidence: EvidencePack,
    journey_index: int,
    code_context: CodeContext,
) -> str:
    """Build investigation prompt focused on a single journey."""
    parts: list[str] = []
    journey = next(
        (j for j in evidence.trip_index.journeys if j.journey_index == journey_index),
        None,
    )
    if journey is None:
        return f"No journey {journey_index} found in trip {evidence.trip_index.trip_id}"

    route = (
        f"{journey.origin} → {journey.destination}"
        if journey.origin else f"journey {journey_index}"
    )
    parts.append(f"## Trip: {evidence.trip_index.trip_id}")
    parts.append(
        f"## Investigating Journey {journey_index}: {route}"
        f" (supplier: {journey.supplier})"
    )

    # Fare chain for this journey
    fare_summary = _build_fare_chain_summary(evidence, journey_filter=journey_index)
    if fare_summary:
        parts.append("\n## Fare Chain Comparison (displayed → held → booked)")
        parts.extend(fare_summary)

    # Divergence point (if for this journey)
    if evidence.divergence_point and evidence.divergence_point.journey_index == journey_index:
        dp = evidence.divergence_point
        parts.append("\n## Divergence Point")
        parts.append(f"Step: {dp.step.value}")
        parts.append(f"Expected: {dp.expected_value}")
        parts.append(f"Actual: {dp.actual_value}")
        parts.append(f"Class: {dp.divergence_class}")
    else:
        parts.append("\n## No divergence found in core baggage chain for this journey")

    # Evidence facts for this journey only
    journey_facts = evidence.facts_by_journey.get(journey_index, [])
    parts.append(f"\n## Evidence ({len(journey_facts)} facts)")
    for fact in journey_facts[:25]:
        parts.append(
            f"- [{fact.step.value}] {fact.label}: {fact.value} "
            f"(source: {fact.citation.source_file} @ {fact.citation.field_path})"
        )

    # Anomalies
    if evidence.anomalies:
        parts.append("\n## Anomalies")
        for a in evidence.anomalies[:10]:
            parts.append(f"- {a}")

    # Red herrings
    if evidence.red_herrings:
        parts.append("\n## Known Red Herrings (DO NOT blame these)")
        for rh in evidence.red_herrings:
            parts.append(f"- {rh}")

    # Code context
    if code_context.files:
        parts.append(f"\n## Relevant Code ({len(code_context.files)} files)")
        for cf in code_context.files[:5]:
            parts.append(f"- {cf.repo}/{cf.path} (functions: {', '.join(cf.functions)})")

    return "\n".join(parts)


def _build_prompt(evidence: EvidencePack, code_context: CodeContext) -> str:
    """Build the investigation prompt from evidence and code context (all journeys)."""
    parts: list[str] = []

    parts.append(f"## Trip: {evidence.trip_index.trip_id}")
    parts.append(f"Supplier: {evidence.supplier}")
    parts.append(f"Journeys: {len(evidence.trip_index.journeys)}")

    # Journey details
    for journey in evidence.trip_index.journeys:
        route = f"{journey.origin} → {journey.destination}" if journey.origin else "unknown"
        parts.append(f"  Journey {journey.journey_index}: {route}")

    # Fare chain comparison per journey
    fare_summary = _build_fare_chain_summary(evidence)
    if fare_summary:
        parts.append("\n## Fare Chain Comparison (displayed → held → booked)")
        parts.extend(fare_summary)

    # Divergence point
    if evidence.divergence_point:
        dp = evidence.divergence_point
        parts.append("\n## Divergence Point")
        parts.append(f"Journey {dp.journey_index}, Step: {dp.step.value}")
        parts.append(f"Expected: {dp.expected_value}")
        parts.append(f"Actual: {dp.actual_value}")
        parts.append(f"Class: {dp.divergence_class}")
        parts.append(f"Citation: {dp.citation.source_file} @ {dp.citation.field_path}")
    else:
        parts.append("\n## No divergence found in the core baggage chain")
        parts.append(
            "All steps are consistent. Investigate if the issue is downstream "
            "or in the ancillary path."
        )

    # Evidence facts grouped by journey
    facts_per_journey = _max_facts_per_journey(evidence)
    for journey in evidence.trip_index.journeys:
        ji = journey.journey_index
        journey_facts = evidence.facts_by_journey.get(ji, [])
        if not journey_facts:
            parts.append(f"\n## Journey {ji} — no evidence facts extracted")
            continue

        parts.append(f"\n## Journey {ji} Evidence ({len(journey_facts)} facts)")
        for fact in journey_facts[:facts_per_journey]:
            parts.append(
                f"- [{fact.step.value}] {fact.label}: {fact.value} "
                f"(source: {fact.citation.source_file} @ {fact.citation.field_path})"
            )
        if len(journey_facts) > facts_per_journey:
            parts.append(f"  ... and {len(journey_facts) - facts_per_journey} more facts")

    # Anomalies
    if evidence.anomalies:
        parts.append(f"\n## Anomalies ({len(evidence.anomalies)})")
        for a in evidence.anomalies[:15]:
            parts.append(f"- {a}")

    # Red herrings
    if evidence.red_herrings:
        parts.append("\n## Known Red Herrings (DO NOT blame these)")
        for rh in evidence.red_herrings:
            parts.append(f"- {rh}")

    # Code context
    if code_context.files:
        parts.append(f"\n## Relevant Code ({len(code_context.files)} files)")
        for cf in code_context.files[:10]:
            parts.append(f"- {cf.repo}/{cf.path} (functions: {', '.join(cf.functions)})")

    if code_context.changed_after_incident:
        parts.append("\n## WARNING: Files changed after incident date")
        for cf in code_context.changed_after_incident:
            parts.append(f"- {cf.repo}/{cf.path}")

    return "\n".join(parts)


def _max_facts_per_journey(evidence: EvidencePack) -> int:
    """Calculate max facts per journey to stay within context budget."""
    n_journeys = max(len(evidence.trip_index.journeys), 1)
    # 30 total facts budget, split evenly across journeys
    return max(10, 30 // n_journeys)


def _build_fare_chain_summary(
    evidence: EvidencePack, *, journey_filter: int | None = None
) -> list[str]:
    """Build a per-journey fare chain: SS1 → SIS-HOLD → SUPPLIER_HOLD → BOOK.

    Extracts fareId, comboFbc, and baggageFareType at each step so the LLM
    can immediately see where the fare switches.

    Args:
        journey_filter: If set, only include this journey index.
    """
    from oncall_rca.schemas.evidence import BaggageChainStep

    lines: list[str] = []

    step_labels = {
        BaggageChainStep.SS1_DISPLAYED: "SS1 (displayed)",
        BaggageChainStep.SIS_HOLD_REQUEST: "SIS-HOLD (held)",
        BaggageChainStep.SUPPLIER_BAGGAGE_HOLD: "SUPPLIER_HOLD (supplier)",
        BaggageChainStep.SUPPLIER_PRICE_HOLD: "SUPPLIER_HOLD (fare)",
        BaggageChainStep.HOLD_CORE_RESPONSE: "HOLD_CORE",
        BaggageChainStep.BOOK_RESPONSE: "BOOK (booked)",
        BaggageChainStep.SUPPLIER_BOOK: "SUPPLIER_BOOK (booked)",
    }

    chain_order = [
        BaggageChainStep.SS1_DISPLAYED,
        BaggageChainStep.SIS_HOLD_REQUEST,
        BaggageChainStep.SUPPLIER_BAGGAGE_HOLD,
        BaggageChainStep.SUPPLIER_PRICE_HOLD,
        BaggageChainStep.HOLD_CORE_RESPONSE,
        BaggageChainStep.SUPPLIER_BOOK,
        BaggageChainStep.BOOK_RESPONSE,
    ]

    for journey in evidence.trip_index.journeys:
        ji = journey.journey_index
        if journey_filter is not None and ji != journey_filter:
            continue
        journey_facts = evidence.facts_by_journey.get(ji, [])
        if not journey_facts:
            continue

        route = f"{journey.origin}→{journey.destination}" if journey.origin else f"journey {ji}"
        lines.append(f"\n### {route}")

        for step in chain_order:
            step_facts = [f for f in journey_facts if f.step == step]
            if not step_facts:
                continue

            label = step_labels.get(step, step.value)
            # Only key facts: fareId, baggageFareType, baggage type, hold/book status
            for fact in step_facts:
                fl = fact.label.lower()
                if any(kw in fl for kw in (
                    "fareid", "faretype", "baggagefaretype", "baggage benefit",
                    "baggage (", "status", "booking status", "hold status",
                )):
                    lines.append(f"  {label}: {fact.label} = {fact.value}")

    return lines
