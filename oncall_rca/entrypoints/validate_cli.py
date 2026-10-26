"""CLI: trip ID → validation report (baggage, fare, FBC checks).

Usage:
    python -m oncall_rca.entrypoints.validate_cli <trip_id> [--offline] [--cache-dir PATH]
    python -m oncall_rca.entrypoints.validate_cli --pdf <path.pdf> [--cache-dir PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from oncall_rca.config.settings import Settings
from oncall_rca.stages.evidence.validator import format_report, validate_trip
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import fetch_file, fetch_trip_index


# API names whose response payloads are needed for validation.
# API names/types whose response payloads are needed for validation.
# Matches against both api and api_type fields — catches internal calls
# (SMS_HOLD, BOOK) AND their external supplier calls (AMADEUS-*, etc.)
# since external calls share the parent's api_type (HOLD, BOOK, TICKETING).
_VALIDATION_APIS = {
    # Internal hold/book
    "SMS_HOLD", "HOLD_CORE", "SUPPLY_CORE_REPO_LAYER",
    "SUPPLIER_BOOK", "SUPPLIER_PRICE_QUOTE", "BOOK",
    # Display layer
    "SINGLE_SOLUTION_SEARCH", "SIS",
    # External supplier call parents (catches AMADEUS-* etc.)
    "HOLD", "TICKETING", "BOOK_SEAT_SELL",
    # Hold chain
    "SOS_HOLD",
}


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

    # Download key payloads needed for validation
    if not args.offline:
        _download_validation_payloads(trip_id, raw_data, cache, settings)

    # Run validation
    report = validate_trip(trip_id, raw_data, cache)
    print(format_report(report))


def _download_validation_payloads(
    trip_id: str,
    raw_data: dict[str, Any],
    cache: TripCache,
    settings: Settings,
) -> None:
    """Download response payloads needed for validation checks."""
    calls = raw_data.get("air_api_call", [])
    header = raw_data.get("header_data", [{}])
    itin = header[0].get("itinerary", [""])[0] if header else ""

    for call in calls:
        api = call.get("api", "")
        api_type = call.get("api_type", "")

        # Only download files relevant to validation
        if api not in _VALIDATION_APIS and api_type not in _VALIDATION_APIS:
            continue

        res_name = call.get("res", "")
        if not res_name:
            continue
        if cache.has_file(trip_id, res_name):
            continue

        # Extract date from the call's time field
        time_str = call.get("time", "")
        date = time_str.split(" ")[0] if time_str else ""
        if not date:
            continue

        try:
            content = fetch_file(res_name, itin, date, trip_id, settings)
            cache.write_file(trip_id, res_name, content)
        except Exception as e:
            print(f"  Warning: could not fetch {res_name}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
