"""Investigator — Groq tool-calling loop to explain WHY the divergence happened.

Receives EvidencePack + CodeContext, forms competing hypotheses,
proves/disproves each with tools. Hard limits on iterations/tokens/time.
"""

from __future__ import annotations

import time
from typing import Any

from oncall_rca.llm.client import LLMClient, LLMBudgetExceeded
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

## Rules
- Every claim MUST cite evidence: (log file + field path) or (code file + line)
- Form competing hypotheses and rule them out with evidence
- If you cannot determine the root cause with high confidence, say so
- Do NOT blame the session-expiry retry (err.2-maxico) — that is expected behaviour
- Do NOT use journeyFareSummary.passengerBaggageDetails — it is fare metadata, not booked baggage

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
    """Run the Investigator to explain the divergence.

    Args:
        evidence: Complete evidence pack with facts and divergence point.
        code_context: Relevant code files.
        llm: LLM client for reasoning.
        max_iterations: Max tool-calling rounds.
        wall_clock_limit: Max wall-clock seconds.

    Returns:
        Hypothesis with root cause explanation and citations.
    """
    start = time.monotonic()

    # Build the prompt with evidence summary
    prompt = _build_prompt(evidence, code_context)

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


def _build_prompt(evidence: EvidencePack, code_context: CodeContext) -> str:
    """Build the investigation prompt from evidence and code context."""
    parts: list[str] = []

    parts.append(f"## Trip: {evidence.trip_index.trip_id}")
    parts.append(f"Supplier: {evidence.supplier}")
    parts.append(f"Journeys: {len(evidence.trip_index.journeys)}")

    # Divergence point
    if evidence.divergence_point:
        dp = evidence.divergence_point
        parts.append(f"\n## Divergence Point")
        parts.append(f"Journey {dp.journey_index}, Step: {dp.step.value}")
        parts.append(f"Expected: {dp.expected_value}")
        parts.append(f"Actual: {dp.actual_value}")
        parts.append(f"Class: {dp.divergence_class}")
        parts.append(f"Citation: {dp.citation.source_file} @ {dp.citation.field_path}")
    else:
        parts.append("\n## No divergence found in the core baggage chain")
        parts.append("All steps are consistent. Investigate if the issue is downstream or in the ancillary path.")

    # Evidence facts
    parts.append(f"\n## Evidence Facts ({len(evidence.facts)} total)")
    for fact in evidence.facts[:30]:  # limit to avoid context overflow
        parts.append(
            f"- [{fact.step.value}] {fact.label}: {fact.value} "
            f"(source: {fact.citation.source_file} @ {fact.citation.field_path})"
        )

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
