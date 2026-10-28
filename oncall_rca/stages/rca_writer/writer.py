"""RCA Writer — generates Markdown RCA document from all stage outputs.

Template matches design §13. Saves to rca_reports/<tripId>/RCA.md + evidence/.
"""

from __future__ import annotations

import json
from pathlib import Path

from oncall_rca.schemas.code_context import CodeContext
from oncall_rca.schemas.evidence import EvidencePack
from oncall_rca.schemas.hypothesis import CriticReport, Hypothesis
from oncall_rca.schemas.incident import Incident
from oncall_rca.schemas.rca_doc import RCADoc, TimelineEntry


def build_rca_doc(
    incident: Incident,
    evidence: EvidencePack,
    code_context: CodeContext,
    hypotheses: dict[int, Hypothesis] | Hypothesis,
    critic_reports: dict[int, CriticReport] | CriticReport,
) -> RCADoc:
    """Build an RCADoc from all stage outputs.

    Accepts per-journey dicts or legacy single objects for backward compat.
    """
    trip_id = evidence.trip_index.trip_id

    # Normalize to dicts
    if isinstance(hypotheses, Hypothesis):
        hypotheses = {0: hypotheses}
    if isinstance(critic_reports, CriticReport):
        critic_reports = {0: critic_reports}

    # Timeline from journey calls
    timeline: list[TimelineEntry] = []
    for journey in evidence.trip_index.journeys:
        for call in sorted(journey.calls or [], key=lambda c: c.time or c.time)[:10]:
            timeline.append(TimelineEntry(
                timestamp=call.time.strftime("%Y-%m-%d %H:%M:%S") if call.time else "",
                service=call.service.value,
                pod=call.pod or call.host,
                description=f"{call.api} ({call.api_type})",
                file_ref=call.res_file.file_name if call.res_file else "",
            ))

    # Combine per-journey root causes
    all_citations = []
    all_alternatives = []
    all_affected_code = []
    root_cause_parts = []
    summary_parts = []
    proposed_fix_parts = []

    # Use worst (lowest) confidence across journeys
    worst_confidence = None
    confidence_rank = {"high": 3, "medium": 2, "low": 1}

    for ji in sorted(set(hypotheses.keys()) | set(critic_reports.keys())):
        h = hypotheses.get(ji)
        cr = critic_reports.get(ji)
        journey = next(
            (j for j in evidence.trip_index.journeys if j.journey_index == ji), None
        )
        route = (
            f"{journey.origin}→{journey.destination}"
            if journey and journey.origin else f"Journey {ji}"
        )

        if h:
            summary_parts.append(f"[{route}] {h.claim}")
            root_cause_parts.append(f"### {route}\n{h.explanation}")
            all_citations.extend(h.supporting_citations)
            all_affected_code.extend(h.affected_code)
            if h.proposed_fix:
                proposed_fix_parts.append(f"**{route}:** {h.proposed_fix}")

        if cr:
            all_alternatives.extend(cr.alternatives)
            rank = confidence_rank.get(cr.confidence.value, 1)
            if worst_confidence is None or rank < confidence_rank.get(worst_confidence.value, 1):
                worst_confidence = cr.confidence

    from oncall_rca.schemas.hypothesis import Confidence
    final_confidence = worst_confidence or Confidence.LOW

    return RCADoc(
        trip_id=trip_id,
        confidence=final_confidence,
        summary=" | ".join(summary_parts) if summary_parts else "No hypothesis generated.",
        reported_symptom=incident.reported_symptom,
        itinerary_ids=evidence.trip_index.itineraries,
        journeys=[
            f"{j.origin}→{j.destination} ({j.supplier})"
            for j in evidence.trip_index.journeys
        ],
        passengers=evidence.trip_index.pax_count,
        timeline=timeline,
        divergence_point=evidence.divergence_point,
        root_cause="\n\n".join(root_cause_parts),
        root_cause_citations=all_citations,
        alternatives=all_alternatives,
        affected_code=all_affected_code,
        commit_shas=code_context.commit_shas,
        changed_after_incident_warning=[
            f"{cf.repo}/{cf.path}" for cf in code_context.changed_after_incident
        ],
        proposed_fix="\n\n".join(proposed_fix_parts) if proposed_fix_parts else "",
        risks=[],
    )


def render_rca_markdown(doc: RCADoc) -> str:
    """Render an RCADoc as Markdown matching the template in design §13."""
    lines: list[str] = []

    lines.append(f"# RCA — Trip {doc.trip_id}\n")

    lines.append("## Summary")
    lines.append(f"{doc.summary} Confidence: **{doc.confidence.value.upper()}**.\n")

    lines.append("## Incident")
    lines.append(f"- **Reported symptom:** {doc.reported_symptom}")
    lines.append(f"- **Itinerary IDs:** {', '.join(doc.itinerary_ids)}")
    lines.append(f"- **Journeys:** {', '.join(doc.journeys)}")
    lines.append(f"- **Passengers:** {doc.passengers}\n")

    lines.append("## Timeline")
    for entry in doc.timeline:
        lines.append(f"| {entry.timestamp} | {entry.service} | {entry.pod} | {entry.description} |")
    lines.append("")

    lines.append("## Divergence Point")
    if doc.divergence_point:
        dp = doc.divergence_point
        lines.append(f"- **Journey:** {dp.journey_index}")
        lines.append(f"- **Step:** {dp.step.value}")
        lines.append(f"- **Expected:** {dp.expected_value}")
        lines.append(f"- **Actual:** {dp.actual_value}")
        lines.append(f"- **Class:** {dp.divergence_class}")
    else:
        lines.append("No divergence found in the core baggage chain.\n")

    lines.append("\n## Root Cause")
    lines.append(doc.root_cause)
    if doc.root_cause_citations:
        lines.append("\n**Citations:**")
        for c in doc.root_cause_citations:
            lines.append(f"- `{c.source_file}` @ `{c.field_path}`: \"{c.excerpt}\"")

    lines.append("\n## Alternatives Considered")
    if doc.alternatives:
        for alt in doc.alternatives:
            lines.append(f"- **{alt.claim}** — {alt.why_ranked_lower}")
    else:
        lines.append("None.\n")

    lines.append("\n## Affected Code")
    for cf in doc.affected_code:
        lines.append(f"- `{cf.repo}/{cf.path}` (functions: {', '.join(cf.functions)})")
    if doc.commit_shas:
        lines.append(f"\nCommit SHAs analysed: {json.dumps(doc.commit_shas)}")
    if doc.changed_after_incident_warning:
        lines.append("\n**⚠️ Files changed after incident date:**")
        for f in doc.changed_after_incident_warning:
            lines.append(f"- {f}")

    lines.append("\n## Proposed Fix")
    lines.append(doc.proposed_fix or "No fix proposed.\n")

    lines.append("\n## Acceptance Criteria")
    for ac in doc.acceptance_criteria:
        lines.append(f"- [ ] {ac}")

    lines.append("\n## Risks")
    for risk in doc.risks:
        lines.append(f"- {risk}")

    return "\n".join(lines)


def save_rca(doc: RCADoc, output_dir: Path) -> Path:
    """Save RCA document and evidence to disk.

    Returns:
        Path to the saved RCA.md file.
    """
    rca_dir = output_dir / doc.trip_id
    evidence_dir = rca_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Write RCA markdown
    rca_path = rca_dir / "RCA.md"
    rca_path.write_text(render_rca_markdown(doc))

    # Write structured data
    (rca_dir / "rca.json").write_text(doc.model_dump_json(indent=2))

    return rca_path
