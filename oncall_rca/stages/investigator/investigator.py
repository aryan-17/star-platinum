"""Investigator — ReAct tool-calling loop to explain WHY the divergence happened.

Receives EvidencePack + CodeContext. Instead of dumping all evidence into one
prompt, the LLM uses per-step tools to fetch only the data it needs. Each tool
returns focused, pre-extracted facts — no raw payloads reach the LLM.

Hard limits on iterations/tokens/time.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from oncall_rca.llm.client import LLMBudgetExceeded, LLMClient
from oncall_rca.schemas.hypothesis import Hypothesis
from oncall_rca.stages.investigator.tools import (
    InvestigatorToolkit,
    build_initial_context,
)

if TYPE_CHECKING:
    from oncall_rca.schemas.code_context import CodeContext
    from oncall_rca.schemas.evidence import EvidencePack

_SYSTEM_PROMPT = """\
You are an expert flight booking systems investigator.

You have tools to fetch evidence about a baggage mismatch incident.
Your job is to explain WHY the divergence happened — not just where.

## How to investigate
1. Start with get_fare_chain — it shows the fare at every step.
2. If the fare switches between steps, drill into those steps using
   the specific tools (get_ss1_facts, get_sis_hold_facts, etc.).
3. Check get_divergence_point for the detected mismatch.
4. Check get_anomalies for validation failures.
5. Use get_code_context if you need to trace the code path.

## Two-worlds principle
supply-core = what the user was shown (displayed world)
air-sms = what was actually booked (booked world)
Compare them. If they disagree, either supply-core built the request
wrongly (our bug) or the supplier returned something different.

## Rules
- Every claim MUST cite evidence: (log file + field path) or (code file + line)
- Form competing hypotheses and rule them out with evidence
- If you cannot determine the root cause with high confidence, say so
- Do NOT blame session-expiry retry (err.2-maxico) — expected behaviour
- Do NOT use journeyFareSummary.passengerBaggageDetails — fare metadata only

## Output
When you have enough evidence, respond with a JSON object (no tool calls).
Do NOT call any more tools once you are ready to answer."""


def investigate_journey(
    evidence: EvidencePack,
    journey_index: int,
    code_context: CodeContext,
    llm: LLMClient,
    *,
    max_iterations: int = 10,
    wall_clock_limit: int = 300,
) -> Hypothesis:
    """Run the Investigator for a single journey using tool-calling.

    The LLM receives minimal seed context (trip ID, route, supplier) and
    per-step tools. It fetches only the evidence it needs on demand.

    Args:
        evidence: Complete evidence pack with facts and divergence point.
        journey_index: Which journey to investigate.
        code_context: Relevant code files.
        llm: LLM client with tool-calling support.
        max_iterations: Max tool-calling rounds.
        wall_clock_limit: Max wall-clock seconds.

    Returns:
        Hypothesis with root cause explanation and citations.
    """
    toolkit = InvestigatorToolkit(evidence, journey_index, code_context)
    seed_prompt = build_initial_context(evidence, journey_index)

    start = time.monotonic()
    try:
        hypothesis = llm.generate_with_tools(
            system=_SYSTEM_PROMPT,
            user_prompt=seed_prompt,
            tools=toolkit.to_tool_definitions(),
            tool_executor=toolkit.execute,
            response_schema=Hypothesis,
            stage="investigator",
            temperature=0.0,
            max_tokens=4096,
            max_iterations=max_iterations,
        )
    except LLMBudgetExceeded:
        return Hypothesis(
            claim="Investigation incomplete — token budget exceeded",
            explanation=(
                "The investigator ran out of token budget before "
                "reaching a conclusion."
            ),
        )

    elapsed = time.monotonic() - start
    if elapsed > wall_clock_limit:
        hypothesis = hypothesis.model_copy(update={
            "claim": f"{hypothesis.claim} (wall-clock limit reached)",
        })

    return hypothesis


def investigate(
    evidence: EvidencePack,
    code_context: CodeContext,
    llm: LLMClient,
    *,
    max_iterations: int = 10,
    wall_clock_limit: int = 300,
) -> Hypothesis:
    """Run the Investigator for the whole trip (legacy single-hypothesis).

    Investigates the first journey only. For multi-journey trips,
    use investigate_journey per journey via the graph node.
    """
    journey_index = (
        evidence.trip_index.journeys[0].journey_index
        if evidence.trip_index.journeys
        else 0
    )
    return investigate_journey(
        evidence,
        journey_index,
        code_context,
        llm,
        max_iterations=max_iterations,
        wall_clock_limit=wall_clock_limit,
    )
