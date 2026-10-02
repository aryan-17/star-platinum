"""Critic — independent verification of the Investigator's hypothesis.

Fresh context: sees evidence + hypothesis, NOT the Investigator's reasoning.
Mechanically verifies citations, lists alternatives, assigns confidence.
"""

from __future__ import annotations

from oncall_rca.llm.client import LLMClient, LLMBudgetExceeded
from oncall_rca.schemas.evidence import EvidencePack
from oncall_rca.schemas.hypothesis import Confidence, CriticReport, Hypothesis


_SYSTEM_PROMPT = """You are an independent RCA critic. You verify hypotheses about flight booking incidents.

## Your job
1. Mechanically verify each citation: does the cited file/field exist and say what is claimed?
2. List alternative explanations the investigator may have missed
3. Assign confidence (high/medium/low) using the rubric below

## Confidence rubric
HIGH: all citations valid, alternatives ruled out, timeline consistent, all symptoms explained
MEDIUM: most citations valid, some alternatives remain, timeline mostly consistent
LOW: citations missing/invalid, key alternatives not addressed, gaps in explanation

## Rules
- You have NOT seen the investigator's reasoning — only the hypothesis and evidence
- Be skeptical but fair
- If the hypothesis is "no divergence found", verify the evidence supports that claim
- Do NOT invent evidence that isn't in the facts list

## Output
Respond with a JSON object matching the CriticReport schema."""


def critique(
    hypothesis: Hypothesis,
    evidence: EvidencePack,
    llm: LLMClient,
) -> CriticReport:
    """Run the Critic to verify a hypothesis.

    Args:
        hypothesis: The Investigator's hypothesis to verify.
        evidence: The evidence pack (same data the Investigator saw).
        llm: LLM client (fresh context — does not share Investigator's history).

    Returns:
        CriticReport with citation checks, alternatives, and confidence.
    """
    prompt = _build_prompt(hypothesis, evidence)

    try:
        report = llm.generate(
            prompt=prompt,
            system=_SYSTEM_PROMPT,
            response_schema=CriticReport,
            stage="critic",
            temperature=0.0,
            max_tokens=4096,
        )
    except LLMBudgetExceeded:
        return CriticReport(
            confidence=Confidence.LOW,
            notes="Critic could not complete — token budget exceeded",
        )

    return report


def _build_prompt(hypothesis: Hypothesis, evidence: EvidencePack) -> str:
    """Build the critic prompt."""
    parts: list[str] = []

    parts.append("## Hypothesis to verify")
    parts.append(f"Claim: {hypothesis.claim}")
    parts.append(f"Explanation: {hypothesis.explanation}")

    if hypothesis.supporting_citations:
        parts.append("\n## Supporting citations")
        for c in hypothesis.supporting_citations:
            parts.append(f"- {c.source_file} @ {c.field_path}: \"{c.excerpt}\"")

    if hypothesis.contradicting_evidence:
        parts.append("\n## Contradicting evidence noted by investigator")
        for c in hypothesis.contradicting_evidence:
            parts.append(f"- {c.source_file} @ {c.field_path}: \"{c.excerpt}\"")

    if hypothesis.affected_code:
        parts.append("\n## Affected code")
        for cf in hypothesis.affected_code:
            parts.append(f"- {cf.repo}/{cf.path}")

    parts.append(f"\n## Available evidence facts ({len(evidence.facts)} total)")
    for fact in evidence.facts[:30]:
        parts.append(
            f"- [{fact.step.value}] {fact.label}: {fact.value} "
            f"(source: {fact.citation.source_file})"
        )

    if evidence.divergence_point:
        dp = evidence.divergence_point
        parts.append(f"\n## Divergence point")
        parts.append(f"Step: {dp.step.value}, Expected: {dp.expected_value}, Actual: {dp.actual_value}")
    else:
        parts.append("\n## No divergence found in core chain")

    return "\n".join(parts)
