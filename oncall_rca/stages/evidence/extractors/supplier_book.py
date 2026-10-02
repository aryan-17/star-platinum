"""Extractor for SUPPLIER_BOOK response — SOAP XML payload."""

from __future__ import annotations

from typing import Any

from oncall_rca.schemas.evidence import BaggageChainStep, Citation, EvidenceFact
from oncall_rca.stages.evidence.extractors.base import register_extractor


# OTA namespace used in Air Arabia SOAP responses
_NS = {"ns1": "http://www.opentravel.org/OTA/2003/05"}


@register_extractor(BaggageChainStep.SUPPLIER_BOOK)
class SupplierBookExtractor:
    """Extract PNR, booking status, baggage, and ticket from SUPPLIER_BOOK SOAP response."""

    step = BaggageChainStep.SUPPLIER_BOOK

    def extract(
        self,
        content: Any,
        *,
        journey_index: int,
        source_file: str,
    ) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []

        # content is an lxml Element
        try:
            root = content
        except Exception:
            return []

        # PNR (BookingReferenceID)
        for ref in root.iter("{http://www.opentravel.org/OTA/2003/05}BookingReferenceID"):
            pnr = ref.get("ID", "")
            if pnr:
                facts.append(EvidenceFact(
                    fact_id=f"j{journey_index}_supplier_pnr",
                    journey_index=journey_index,
                    step=self.step,
                    label="SUPPLIER_BOOK PNR",
                    value=pnr,
                    citation=Citation(
                        source_file=source_file,
                        field_path="BookingReferenceID/@ID",
                        excerpt=pnr,
                    ),
                ))
                break

        # Success indicator
        for success in root.iter("{http://www.opentravel.org/OTA/2003/05}Success"):
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_supplier_success",
                journey_index=journey_index,
                step=self.step,
                label="SUPPLIER_BOOK success",
                value="true",
                citation=Citation(
                    source_file=source_file,
                    field_path="Success",
                    excerpt="<Success/>",
                ),
            ))
            break

        # Errors
        for error in root.iter("{http://www.opentravel.org/OTA/2003/05}Error"):
            error_code = error.get("Code", "")
            error_msg = error.text or error.get("ShortText", "")
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_supplier_error",
                journey_index=journey_index,
                step=self.step,
                label="SUPPLIER_BOOK error",
                value=f"{error_code}: {error_msg}",
                citation=Citation(
                    source_file=source_file,
                    field_path="Error/@Code",
                    excerpt=f"{error_code}: {error_msg}"[:60],
                ),
            ))

        # Baggage from BaggageRequest elements
        for bag_req in root.iter("{http://www.opentravel.org/OTA/2003/05}BaggageRequest"):
            baggage_code = bag_req.get("baggageCode", "")
            flight = bag_req.get("FlightNumber", "")
            if baggage_code:
                facts.append(EvidenceFact(
                    fact_id=f"j{journey_index}_supplier_baggage_{flight}",
                    journey_index=journey_index,
                    step=self.step,
                    label=f"SUPPLIER_BOOK baggage ({flight})",
                    value=baggage_code,
                    citation=Citation(
                        source_file=source_file,
                        field_path=f"BaggageRequest[@FlightNumber='{flight}']/@baggageCode",
                        excerpt=baggage_code,
                    ),
                ))

        # E-ticket
        for tkt in root.iter("{http://www.opentravel.org/OTA/2003/05}ETicketInfomation"):
            ticket_no = tkt.get("eTicketNo", "")
            if ticket_no:
                facts.append(EvidenceFact(
                    fact_id=f"j{journey_index}_supplier_ticket",
                    journey_index=journey_index,
                    step=self.step,
                    label="SUPPLIER_BOOK e-ticket",
                    value=ticket_no,
                    citation=Citation(
                        source_file=source_file,
                        field_path="ETicketInfomation/@eTicketNo",
                        excerpt=ticket_no,
                    ),
                ))
                break

        # FareBasisCode
        for fbc in root.iter("{http://www.opentravel.org/OTA/2003/05}FareBasisCode"):
            if fbc.text:
                facts.append(EvidenceFact(
                    fact_id=f"j{journey_index}_supplier_fbc",
                    journey_index=journey_index,
                    step=self.step,
                    label="SUPPLIER_BOOK fare basis code",
                    value=fbc.text,
                    citation=Citation(
                        source_file=source_file,
                        field_path="FareBasisCode",
                        excerpt=fbc.text,
                    ),
                ))
                break

        return facts
