"""CLI: trip ID or mail PDF → complete RCA document.

Usage:
    python -m oncall_rca.entrypoints.rca_cli <trip_id> [--offline] [--cache-dir PATH] [--output-dir PATH]
    python -m oncall_rca.entrypoints.rca_cli --pdf <path.pdf> [--cache-dir PATH] [--output-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from oncall_rca.workflow.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate RCA for a trip")
    parser.add_argument("trip_id", nargs="?", help="12-13 digit trip reference")
    parser.add_argument("--pdf", type=Path, help="Path to Gmail PDF export")
    parser.add_argument("--offline", action="store_true", help="Read from cache only")
    parser.add_argument("--cache-dir", type=Path, help="Override cache directory")
    parser.add_argument("--output-dir", type=Path, help="Override RCA output directory")
    args = parser.parse_args()

    incident = None
    trip_id = args.trip_id

    if args.pdf:
        from oncall_rca.stages.intake.pdf_mail import extract_from_pdf
        incident = extract_from_pdf(args.pdf)
        trip_id = incident.trip_ref
        print(f"Extracted from PDF:")
        print(f"  Trip ref: {incident.trip_ref}")
        print(f"  Type: {incident.incident_type.value}")
        print(f"  Symptom: {incident.reported_symptom[:100]}...")
    elif not trip_id:
        parser.error("Either trip_id or --pdf is required")

    state = run_pipeline(
        trip_id,
        incident=incident,
        offline=args.offline,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
    )

    # Report
    print(f"\nRun: {state.run_id}")
    for record in state.stages:
        status_icon = {"completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(record.status.value, "⏳")
        print(f"  {status_icon} {record.stage}: {record.status.value}")
        if record.error:
            print(f"     Error: {record.error}")

    if state.rca_doc:
        print(f"\nRCA generated: Confidence = {state.rca_doc.confidence.value.upper()}")
        print(f"Summary: {state.rca_doc.summary}")
    else:
        print("\nNo RCA generated.")


if __name__ == "__main__":
    main()
