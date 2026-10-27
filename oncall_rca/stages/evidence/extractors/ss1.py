"""Extractor for SINGLE_SOLUTION_SEARCH (SS1) — JSON payload.

Extracts per-fare-family baggage info from fareBenefits and baggageFareType.
Also compares multiple SS1 calls for consistency (SS1_CONSISTENCY).

Real structure:
  flightSolutions[].fareFamilies[].journeyFareInfo.<route>.baggageFareType
  flightSolutions[].fareFamilies[].fareBenefits[].baggageAllowance
  flightSolutions[].fareFamilies[].comboFbc
"""

from __future__ import annotations

from typing import Any

from oncall_rca.schemas.evidence import BaggageChainStep, Citation, EvidenceFact
from oncall_rca.stages.evidence.extractors.base import register_extractor


@register_extractor(BaggageChainStep.SS1_DISPLAYED)
class SS1DisplayedExtractor:
    """Extract displayed baggage info from SINGLE_SOLUTION_SEARCH response."""

    step = BaggageChainStep.SS1_DISPLAYED

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
        solutions = content.get("flightSolutions", [])
        if not isinstance(solutions, list):
            return []

        for sol in solutions:
            if not isinstance(sol, dict):
                continue
            for ff in sol.get("fareFamilies", []):
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

        # baggageFareType from journeyFareInfo
        journey_fare_info = ff.get("journeyFareInfo", {})
        if isinstance(journey_fare_info, dict):
            for route, jfi in journey_fare_info.items():
                if not isinstance(jfi, dict):
                    continue
                bag_fare_type = jfi.get("baggageFareType", "")
                if bag_fare_type:
                    fid = f"j{journey_index}_ss1_{_safe_id(combo_fbc)}_bagtype"
                    facts.append(EvidenceFact(
                        fact_id=fid,
                        journey_index=journey_index,
                        step=self.step,
                        label=f"SS1 baggageFareType ({group_name}, {combo_fbc})",
                        value=bag_fare_type,
                        citation=Citation(
                            source_file=source_file,
                            field_path=(
                                f"flightSolutions[].fareFamilies[comboFbc={combo_fbc}]"
                                f".journeyFareInfo.{route}.baggageFareType"
                            ),
                            excerpt=bag_fare_type,
                        ),
                    ))

        # fareBenefits — extract BAGGAGE benefits
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
                sector = benefit.get("sector", "")

                value_parts = [f"type={bag_type}"]
                if weight_kg is not None:
                    value_parts.append(f"weight={weight_kg}kg")
                if pieces is not None:
                    value_parts.append(f"pieces={int(pieces)}")
                value = ", ".join(value_parts)

                fid = f"j{journey_index}_ss1_{_safe_id(combo_fbc)}_{bag_type.lower()}"
                facts.append(EvidenceFact(
                    fact_id=fid,
                    journey_index=journey_index,
                    step=self.step,
                    label=f"SS1 baggage benefit ({group_name}, {combo_fbc}, {sector})",
                    value=value,
                    citation=Citation(
                        source_file=source_file,
                        field_path=(
                            f"flightSolutions[].fareFamilies[comboFbc={combo_fbc}]"
                            f".fareBenefits[benefitType=BAGGAGE,baggageType={bag_type}]"
                        ),
                        excerpt=value,
                    ),
                ))

        return facts


def _find_measurement(
    measurements: list[Any], mtype: str, unit: str
) -> float | None:
    """Find a measurement value by type and unit."""
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


def _safe_id(s: str) -> str:
    """Make a string safe for use in fact IDs."""
    return s.replace(",", "_").replace("|", "_").replace(" ", "_")[:40]
