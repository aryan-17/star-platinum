"""Extractor for BOOK response — JSON payload (me-air-sms layer)."""

from __future__ import annotations

from typing import Any

from oncall_rca.schemas.evidence import BaggageChainStep, Citation, EvidenceFact
from oncall_rca.stages.evidence.extractors.base import register_extractor


@register_extractor(BaggageChainStep.BOOK_RESPONSE)
class BookResponseExtractor:
    """Extract booking status, PNR, and ticket from BOOK response."""

    step = BaggageChainStep.BOOK_RESPONSE

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

        # Booking status
        status = content.get("bookingStatus", "")
        if status:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_book_status",
                journey_index=journey_index,
                step=self.step,
                label="BOOK booking status",
                value=str(status),
                citation=Citation(
                    source_file=source_file,
                    field_path="bookingStatus",
                    excerpt=str(status),
                ),
            ))

        # Supplier PNR
        pnr = content.get("supplierPnr", "")
        if pnr:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_book_pnr",
                journey_index=journey_index,
                step=self.step,
                label="BOOK supplier PNR",
                value=str(pnr),
                citation=Citation(
                    source_file=source_file,
                    field_path="supplierPnr",
                    excerpt=str(pnr),
                ),
            ))

        # Ticket number
        ticket = content.get("ticketNumber", "")
        if ticket:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_book_ticket",
                journey_index=journey_index,
                step=self.step,
                label="BOOK ticket number",
                value=str(ticket),
                citation=Citation(
                    source_file=source_file,
                    field_path="ticketNumber",
                    excerpt=str(ticket),
                ),
            ))

        return facts
