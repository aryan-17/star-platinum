"""CLI: trip ID → validation report (baggage, fare, FBC checks).

Usage:
    python -m oncall_rca.entrypoints.validate_cli <trip_id> [--offline] [--cache-dir PATH]
    python -m oncall_rca.entrypoints.validate_cli --pdf <path.pdf> [--cache-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from oncall_rca.config.settings import Settings
from oncall_rca.stages.evidence.validator import format_report, validate_trip
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import fetch_trip_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a trip for baggage/fare issues")
    parser.add_argument("trip_id", nargs="?", help="12-13 digit trip reference")
    parser.add_argument("--pdf", type=Path, help="Path to Gmail PDF export (extracts trip ID)")
    parser.add_argument("--offline", action="store_true", help="Read from cache only")
    parser.add_argument("--cache-dir", type=Path, help="Override cache directory")
    args = parser.parse_args()

    trip_id = args.trip_id
    if args.pdf:
        from oncall_rca.stages.intake.pdf_mail import extract_from_pdf
        incident = extract_from_pdf(args.pdf)
        trip_id = incident.trip_ref
        print(f"Extracted trip ref from PDF: {trip_id}\n")
    elif not trip_id:
        parser.error("Either trip_id or --pdf is required")

    settings = Settings()
    cache = TripCache(args.cache_dir or settings.output.cache_dir)

    # Get trip data
    if cache.has_trip_index(trip_id):
        raw_data = cache.read_trip_index(trip_id)
    elif args.offline:
        print(f"ERROR: Trip {trip_id} not in cache and --offline specified.", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Fetching trip {trip_id} from API...")
        raw_data = fetch_trip_index(trip_id, settings)
        cache.write_trip_index(trip_id, raw_data)

    # Run validation
    report = validate_trip(trip_id, raw_data, cache)
    print(format_report(report))


if __name__ == "__main__":
    main()
