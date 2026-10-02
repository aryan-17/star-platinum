"""CLI: trip ID → normalised timeline with journey split.

Usage:
    python -m oncall_rca.entrypoints.timeline_cli <trip_id> [--offline] [--cache-dir PATH]

Examples:
    python -m oncall_rca.entrypoints.timeline_cli 260802431929
    python -m oncall_rca.entrypoints.timeline_cli 260802431929 --offline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from oncall_rca.config.settings import Settings
from oncall_rca.stages.evidence.call_tree import build_call_tree, detect_supplier, split_journeys
from oncall_rca.stages.evidence.index_normaliser import normalise_trip_index
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import fetch_trip_index


def build_timeline(trip_id: str, *, offline: bool = False, cache_dir: Path | None = None) -> dict:
    """Build normalised timeline for a trip.

    Args:
        trip_id: 12-digit trip reference.
        offline: If True, read from cache only.
        cache_dir: Override cache directory.

    Returns:
        Serialisable dict with timeline data.
    """
    settings = Settings()
    cache = TripCache(cache_dir or settings.output.cache_dir)

    # Get raw data — from cache or API
    if cache.has_trip_index(trip_id):
        raw_data = cache.read_trip_index(trip_id)
    elif offline:
        print(f"ERROR: Trip {trip_id} not in cache and --offline specified.", file=sys.stderr)
        sys.exit(1)
    else:
        raw_data = fetch_trip_index(trip_id, settings)
        cache.write_trip_index(trip_id, raw_data)

    # Normalise → call tree → journey split → supplier detection
    trip_index = normalise_trip_index(raw_data)
    trip_index = build_call_tree(trip_index)
    trip_index = split_journeys(trip_index)
    trip_index = detect_supplier(trip_index)

    return _format_timeline(trip_index)


def _format_timeline(trip_index) -> dict:
    """Format TripIndex into a readable timeline dict."""
    result = {
        "trip_id": trip_index.trip_id,
        "itineraries": trip_index.itineraries,
        "channel": trip_index.channel,
        "pax_count": trip_index.pax_count,
        "total_calls": len(trip_index.all_calls),
        "total_files": len(trip_index.file_inventory),
        "anomalies": trip_index.anomalies,
        "journeys": [],
    }

    for j in trip_index.journeys:
        journey_data = {
            "index": j.journey_index,
            "route": f"{j.origin}→{j.destination}" if j.origin else "unknown",
            "supplier": j.supplier,
            "supplier_detected_by": j.supplier_detected_by,
            "calls": len(j.calls),
            "timeline": [],
        }

        # Sort calls by time
        sorted_calls = sorted(j.calls, key=lambda c: c.time or c.time)
        for call in sorted_calls:
            entry = {
                "time": call.time.strftime("%H:%M:%S.%f")[:-3] if call.time else "??:??:??.???",
                "api": call.api,
                "service": call.service.value,
                "duration_ms": call.duration_ms,
                "host": call.host,
            }
            flags = []
            if call.is_external:
                flags.append("EXTERNAL")
            if call.is_retry:
                flags.append("RETRY")
            if call.is_slow:
                flags.append("SLOW")
            if call.is_timeout:
                flags.append("TIMEOUT")
            if flags:
                entry["flags"] = flags
            journey_data["timeline"].append(entry)

        result["journeys"].append(journey_data)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalised trip timeline")
    parser.add_argument("trip_id", help="12-digit trip reference")
    parser.add_argument("--offline", action="store_true", help="Read from cache only")
    parser.add_argument("--cache-dir", type=Path, help="Override cache directory")
    args = parser.parse_args()

    timeline = build_timeline(args.trip_id, offline=args.offline, cache_dir=args.cache_dir)
    print(json.dumps(timeline, indent=2))


if __name__ == "__main__":
    main()
