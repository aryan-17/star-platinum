"""SS1 consistency checker — compares baggage facts across multiple SS1 calls.

Not a per-file extractor. Called after SS1 extraction to detect inconsistencies
between multiple SINGLE_SOLUTION_SEARCH responses for the same journey.
"""

from __future__ import annotations

from oncall_rca.schemas.evidence import BaggageChainStep, Citation, EvidenceFact


def check_ss1_consistency(
    ss1_facts: list[EvidenceFact],
    journey_index: int,
) -> list[EvidenceFact]:
    """Compare SS1 facts from multiple calls for the same journey.

    Groups baggage facts by fare family (comboFbc) and checks if baggageFareType
    is consistent across all SS1 calls.

    Args:
        ss1_facts: All SS1_DISPLAYED facts for this journey.
        journey_index: Which journey to check.

    Returns:
        SS1_CONSISTENCY facts — one per inconsistency found, or a single PASS.
    """
    journey_facts = [
        f for f in ss1_facts
        if f.journey_index == journey_index and f.step == BaggageChainStep.SS1_DISPLAYED
    ]

    if len(journey_facts) < 2:
        return []  # need ≥2 facts to compare

    # Group baggageFareType facts by comboFbc (extracted from label)
    bagtype_by_source: dict[str, list[EvidenceFact]] = {}
    for fact in journey_facts:
        if "baggageFareType" in fact.label:
            source = fact.citation.source_file
            bagtype_by_source.setdefault(source, []).append(fact)

    if len(bagtype_by_source) < 2:
        return []  # all from same file

    # Compare across sources: same comboFbc should have same value
    facts: list[EvidenceFact] = []
    seen_combos: dict[str, dict[str, str]] = {}  # combo → {source: value}

    for source, source_facts in bagtype_by_source.items():
        for fact in source_facts:
            # Extract comboFbc from label pattern "SS1 baggageFareType (group, combo)"
            combo = _extract_combo_from_label(fact.label)
            if not combo:
                continue
            seen_combos.setdefault(combo, {})[source] = fact.value

    for combo, source_values in seen_combos.items():
        unique_values = set(source_values.values())
        if len(unique_values) > 1:
            detail = "; ".join(
                f"{src}: {val}" for src, val in sorted(source_values.items())
            )
            facts.append(EvidenceFact(
                fact_id=f"j{journey_index}_ss1_consistency_{_safe_id(combo)}",
                journey_index=journey_index,
                step=BaggageChainStep.SS1_CONSISTENCY,
                label=f"SS1 consistency check ({combo})",
                value=f"INCONSISTENT: {detail}",
                citation=Citation(
                    source_file=", ".join(sorted(source_values.keys())),
                    field_path="baggageFareType comparison",
                    excerpt=detail,
                ),
            ))

    if not facts:
        facts.append(EvidenceFact(
            fact_id=f"j{journey_index}_ss1_consistency_pass",
            journey_index=journey_index,
            step=BaggageChainStep.SS1_CONSISTENCY,
            label="SS1 consistency check",
            value="PASS: all SS1 calls consistent",
            citation=Citation(
                source_file=", ".join(sorted(bagtype_by_source.keys())),
                field_path="baggageFareType comparison",
                excerpt="consistent across all SS1 calls",
            ),
        ))

    return facts


def _extract_combo_from_label(label: str) -> str:
    """Extract comboFbc from label like 'SS1 baggageFareType (VALUE, TLN10V2T,TLN10V2T)'."""
    if "(" not in label or ")" not in label:
        return ""
    inner = label[label.index("(") + 1:label.rindex(")")]
    parts = inner.split(", ", 1)
    return parts[1] if len(parts) > 1 else ""


def _safe_id(s: str) -> str:
    return s.replace(",", "_").replace("|", "_").replace(" ", "_")[:40]
