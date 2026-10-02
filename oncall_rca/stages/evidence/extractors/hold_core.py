"""Extractor for HOLD_CORE response — JSON payload.

Real structure: {journeyId, holdResponse: {success, responseCode, description, holdID}, holdContext}
"""

from __future__ import annotations

from typing import Any

from oncall_rca.schemas.evidence import BaggageChainStep, Citation, EvidenceFact
from oncall_rca.stages.evidence.extractors.base import register_extractor


@register_extractor(BaggageChainStep.HOLD_CORE_RESPONSE)
class HoldCoreExtractor:
    """Extract hold status, holdID, and journey from HOLD_CORE response."""

    step = BaggageChainStep.HOLD_CORE_RESPONSE

    def extract(
        self,
        content: Any,
        *,
        journey_index: int,
        source_file: str,
    ) -> list[EvidenceFact]:
        if not isinstance(content, dict):
            return []

        facts: list[EvidenceFact] = []
        hold_resp = content.get("holdResponse", {})

        # Hold status from holdResponse.description
        description = hold_resp.get("description", "") if isinstance(hold_resp, dict) else ""
        if description:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_hold_status",
                journey_index=journey_index,
                step=self.step,
                label="HOLD_CORE hold status",
                value=str(description),
                citation=Citation(
                    source_file=source_file,
                    field_path="holdResponse.description",
                    excerpt=str(description),
                ),
            ))

        # Hold ID
        hold_id = hold_resp.get("holdID", "") if isinstance(hold_resp, dict) else ""
        if hold_id:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_hold_id",
                journey_index=journey_index,
                step=self.step,
                label="HOLD_CORE hold ID",
                value=str(hold_id)[:80],
                citation=Citation(
                    source_file=source_file,
                    field_path="holdResponse.holdID",
                    excerpt=str(hold_id)[:60],
                ),
            ))

        # Journey ID (contains route info)
        journey_id = content.get("journeyId", "")
        if journey_id:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_hold_journey",
                journey_index=journey_index,
                step=self.step,
                label="HOLD_CORE journey ID",
                value=str(journey_id)[:80],
                citation=Citation(
                    source_file=source_file,
                    field_path="journeyId",
                    excerpt=str(journey_id)[:60],
                ),
            ))

        return facts
