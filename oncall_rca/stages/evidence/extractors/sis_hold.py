"""Extractor for SIS-HOLD response — JSON payload.

Extracts fare/FBC/baggage from the hold response returned by SIS.
Maps to BaggageChainStep.SIS_HOLD_REQUEST.

Real structure:
  flightSolution.fareFamilies[].journeyFareInfo.<route>.baggageFareType
  flightSolution.fareFamilies[].fareBenefits[].baggageAllowance
  flightSolution.fareFamilies[].comboFbc
  flightSolution.fareFamilies[].fareMetaInfo.fareId
"""

from __future__ import annotations

from typing import Any

from oncall_rca.schemas.evidence import BaggageChainStep, Citation, EvidenceFact
from oncall_rca.stages.evidence.extractors.base import register_extractor


@register_extractor(BaggageChainStep.SIS_HOLD_REQUEST)
class SISHoldExtractor:
    """Extract baggage/fare info from SIS-HOLD response."""

    step = BaggageChainStep.SIS_HOLD_REQUEST

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

        flight_solution = content.get("flightSolution", {})
        if not isinstance(flight_solution, dict):
            return []

        for ff in flight_solution.get("fareFamilies", []):
            if not isinstance(ff, dict):
                continue
            facts.extend(self._extract_fare_family(ff, journey_index, source_file))

        return facts

    def _extract_fare_family(
        self,
        ff: dict[str, Any],
        journey_index: int,
        source_file: str,
    ) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        combo_fbc = ff.get("comboFbc", "unknown")
        group_name = ff.get("groupName", "unknown")
        fare_id = ff.get("fareMetaInfo", {}).get("fareId", "") if isinstance(
            ff.get("fareMetaInfo"), dict
        ) else ""

        # Fare ID
        if fare_id:
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_sishold_fareid",
                journey_index=journey_index,
                step=self.step,
                label=f"SIS-HOLD fareId ({group_name})",
                value=fare_id,
                citation=Citation(
                    source_file=source_file,
                    field_path=f"flightSolution.fareFamilies[comboFbc={combo_fbc}].fareMetaInfo.fareId",
                    excerpt=fare_id,
                ),
            ))

        # baggageFareType from journeyFareInfo
        journey_fare_info = ff.get("journeyFareInfo", {})
        if isinstance(journey_fare_info, dict):
            for route, jfi in journey_fare_info.items():
                if not isinstance(jfi, dict):
                    continue
                bag_fare_type = jfi.get("baggageFareType", "")
                if bag_fare_type:
                    facts.append(EvidenceFact(
                        fact_id=f"j{journey_index}_sishold_bagtype",
                        journey_index=journey_index,
                        step=self.step,
                        label=f"SIS-HOLD baggageFareType ({group_name}, {combo_fbc})",
                        value=bag_fare_type,
                        citation=Citation(
                            source_file=source_file,
                            field_path=(
                                f"flightSolution.fareFamilies[comboFbc={combo_fbc}]"
                                f".journeyFareInfo.{route}.baggageFareType"
                            ),
                            excerpt=bag_fare_type,
                        ),
                    ))

        # fareBenefits — BAGGAGE type
        benefits = ff.get("fareBenefits", [])
        if isinstance(benefits, list):
            for benefit in benefits:
                if not isinstance(benefit, dict):
                    continue
                if benefit.get("benefitType") != "BAGGAGE":
                    continue

                allowance = benefit.get("baggageAllowance", {})
                if not isinstance(allowance, dict):
                    continue

                bag_type = allowance.get("baggageType", "UNKNOWN")
                measurements = allowance.get("measurements", [])
                weight_kg = _find_measurement(measurements, "Weight", "KG")
                pieces = _find_measurement(measurements, "PIECE", "PIECE")

                value_parts = [f"type={bag_type}"]
                if weight_kg is not None:
                    value_parts.append(f"weight={weight_kg}kg")
                if pieces is not None:
                    value_parts.append(f"pieces={int(pieces)}")
                value = ", ".join(value_parts)

                facts.append(EvidenceFact(
                    fact_id=f"j{journey_index}_sishold_{bag_type.lower()}",
                    journey_index=journey_index,
                    step=self.step,
                    label=f"SIS-HOLD baggage benefit ({group_name}, {bag_type})",
                    value=value,
                    citation=Citation(
                        source_file=source_file,
                        field_path=(
                            f"flightSolution.fareFamilies[comboFbc={combo_fbc}]"
                            f".fareBenefits[benefitType=BAGGAGE,baggageType={bag_type}]"
                        ),
                        excerpt=value,
                    ),
                ))

        return facts


def _find_measurement(
    measurements: list[Any], mtype: str, unit: str
) -> float | None:
    if not isinstance(measurements, list):
        return None
    for m in measurements:
        if not isinstance(m, dict):
            continue
        if m.get("type") == mtype or m.get("unit") == unit:
            val = m.get("value")
            if val is not None:
                return float(val)
    return None
