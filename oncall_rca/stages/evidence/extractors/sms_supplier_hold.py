"""Extractor for SMS_SUPPLIER_HOLD response — JSON payload.

Covers two baggage chain steps:
  - SUPPLIER_BAGGAGE_HOLD: baggage info from the supplier hold response
  - SUPPLIER_PRICE_HOLD: fare/FBC from the supplier hold response

Real structure:
  holdResponse.success / .description
  holdResponse.updatedPromise.fareDetails.id (fareId)
  holdResponse.updatedPromise.fareDetails.fareBasicInfo.fareType / .fareGroupName
  holdResponse.updatedPromise.fareDetails.journeyFareSummary[].passengerBaggageDetails
  holdResponse.updatedPromise.fareDetails.journeyFareSummary[].fareSummary.totalAmount
  holdResponse.updatedPromise.fareDetails.tripFareSummary.totalAmount
"""

from __future__ import annotations

from typing import Any

from oncall_rca.schemas.evidence import BaggageChainStep, Citation, EvidenceFact
from oncall_rca.stages.evidence.extractors.base import register_extractor


@register_extractor(BaggageChainStep.SUPPLIER_BAGGAGE_HOLD)
class SupplierBaggageHoldExtractor:
    """Extract baggage info from SMS_SUPPLIER_HOLD response."""

    step = BaggageChainStep.SUPPLIER_BAGGAGE_HOLD

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
        if not isinstance(hold_resp, dict):
            return []

        # Hold success status
        success = hold_resp.get("success")
        description = hold_resp.get("description", "")
        if success is not None:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_smsh_status",
                journey_index=journey_index,
                step=self.step,
                label="SMS_SUPPLIER_HOLD status",
                value=str(description) if description else str(success),
                citation=Citation(
                    source_file=source_file,
                    field_path="holdResponse.description",
                    excerpt=str(description),
                ),
            ))

        promise = hold_resp.get("updatedPromise", {})
        if not isinstance(promise, dict):
            return facts

        fare_details = promise.get("fareDetails", {})
        if not isinstance(fare_details, dict):
            return facts

        # passengerBaggageDetails from journeyFareSummary
        for jfs in fare_details.get("journeyFareSummary", []):
            if not isinstance(jfs, dict):
                continue
            pax_bag = jfs.get("passengerBaggageDetails", [])
            if not isinstance(pax_bag, list):
                continue

            for pbd in pax_bag:
                if not isinstance(pbd, dict):
                    continue
                pax_type = pbd.get("passengerType", "UNKNOWN")
                bag_details = pbd.get("baggageDetails", [])

                if not bag_details:
                    facts.append(EvidenceFact(
                        fact_id=f"j{journey_index}_smsh_bag_{pax_type.lower()}",
                        journey_index=journey_index,
                        step=self.step,
                        label=f"SMS_SUPPLIER_HOLD baggage ({pax_type})",
                        value="NO_BAGGAGE: baggageDetails is empty",
                        citation=Citation(
                            source_file=source_file,
                            field_path=(
                                "holdResponse.updatedPromise.fareDetails"
                                f".journeyFareSummary[].passengerBaggageDetails"
                                f"[passengerType={pax_type}].baggageDetails"
                            ),
                            excerpt="baggageDetails: []",
                        ),
                    ))
                else:
                    for bd in bag_details:
                        if not isinstance(bd, dict):
                            continue
                        bag_type = bd.get("baggageType", "UNKNOWN")
                        weight = bd.get("weight", "")
                        unit = bd.get("unit", "")
                        pieces = bd.get("pieces", "")
                        value = f"type={bag_type}"
                        if weight:
                            value += f", weight={weight}{unit}"
                        if pieces:
                            value += f", pieces={pieces}"

                        facts.append(EvidenceFact(
                            fact_id=f"j{journey_index}_smsh_bag_{pax_type.lower()}_{bag_type.lower()}",
                            journey_index=journey_index,
                            step=self.step,
                            label=f"SMS_SUPPLIER_HOLD baggage ({pax_type}, {bag_type})",
                            value=value,
                            citation=Citation(
                                source_file=source_file,
                                field_path=(
                                    "holdResponse.updatedPromise.fareDetails"
                                    f".journeyFareSummary[].passengerBaggageDetails"
                                    f"[passengerType={pax_type}].baggageDetails"
                                ),
                                excerpt=value,
                            ),
                        ))

        return facts


@register_extractor(BaggageChainStep.SUPPLIER_PRICE_HOLD)
class SupplierPriceHoldExtractor:
    """Extract fare/FBC info from SMS_SUPPLIER_HOLD response."""

    step = BaggageChainStep.SUPPLIER_PRICE_HOLD

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
        if not isinstance(hold_resp, dict):
            return []

        promise = hold_resp.get("updatedPromise", {})
        if not isinstance(promise, dict):
            return facts

        fare_details = promise.get("fareDetails", {})
        if not isinstance(fare_details, dict):
            return facts

        # fareId
        fare_id = fare_details.get("id", "")
        if fare_id:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_smsh_fareid",
                journey_index=journey_index,
                step=self.step,
                label="SMS_SUPPLIER_HOLD fareId",
                value=str(fare_id),
                citation=Citation(
                    source_file=source_file,
                    field_path="holdResponse.updatedPromise.fareDetails.id",
                    excerpt=str(fare_id),
                ),
            ))

        # fareBasicInfo
        fbi = fare_details.get("fareBasicInfo", {})
        if isinstance(fbi, dict):
            fare_type = fbi.get("fareType", "")
            fare_group = fbi.get("fareGroupName", "")
            fare_name = fbi.get("fareName", "")
            if fare_type:
                facts.append(EvidenceFact(
                    fact_id=f"j{journey_index}_smsh_faretype",
                    journey_index=journey_index,
                    step=self.step,
                    label="SMS_SUPPLIER_HOLD fareType",
                    value=f"name={fare_name}, type={fare_type}, group={fare_group}",
                    citation=Citation(
                        source_file=source_file,
                        field_path="holdResponse.updatedPromise.fareDetails.fareBasicInfo",
                        excerpt=f"{fare_name}/{fare_type}/{fare_group}",
                    ),
                ))

        # Total fare amount from tripFareSummary
        trip_fare = fare_details.get("tripFareSummary", {})
        if isinstance(trip_fare, dict):
            total = trip_fare.get("totalAmount")
            if total is not None:
                facts.append(EvidenceFact(
                    fact_id=f"j{journey_index}_smsh_total",
                    journey_index=journey_index,
                    step=self.step,
                    label="SMS_SUPPLIER_HOLD total fare",
                    value=str(total),
                    citation=Citation(
                        source_file=source_file,
                        field_path="holdResponse.updatedPromise.fareDetails.tripFareSummary.totalAmount",
                        excerpt=str(total),
                    ),
                ))

        return facts
