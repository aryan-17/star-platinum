"""Local file cache — immutable once written, supports offline mode.

Layout: cache/<tripId>/trip_index.json
        cache/<tripId>/files/<filename>.dat
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oncall_rca.config.settings import Settings


class CacheError(Exception):
    """Cache operation failed."""


class CacheMiss(CacheError):
    """Requested data not in cache."""


class TripCache:
    """Immutable local cache for trip data.

    Once a file is written, it is never overwritten.
    Offline mode reads exclusively from cache — no network calls.
    """

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir

    @classmethod
    def from_settings(cls, settings: Settings) -> TripCache:
        return cls(settings.output.cache_dir)

    def trip_dir(self, trip_id: str) -> Path:
        return self._cache_dir / trip_id

    # ── Trip index ──────────────────────────────────────────────

    def has_trip_index(self, trip_id: str) -> bool:
        return (self.trip_dir(trip_id) / "trip_index.json").exists()

    def read_trip_index(self, trip_id: str) -> dict[str, Any]:
        path = self.trip_dir(trip_id) / "trip_index.json"
        if not path.exists():
            raise CacheMiss(f"Trip index not cached for {trip_id}")
        return json.loads(path.read_text())

    def write_trip_index(self, trip_id: str, data: dict[str, Any]) -> None:
        path = self.trip_dir(trip_id) / "trip_index.json"
        if path.exists():
            return  # immutable — never overwrite
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    # ── Individual files ────────────────────────────────────────

    def has_file(self, trip_id: str, file_name: str) -> bool:
        return self._file_path(trip_id, file_name).exists()

    def read_file(self, trip_id: str, file_name: str) -> bytes:
        path = self._file_path(trip_id, file_name)
        if not path.exists():
            raise CacheMiss(f"File not cached: {trip_id}/{file_name}")
        return path.read_bytes()

    def write_file(self, trip_id: str, file_name: str, content: bytes) -> None:
        path = self._file_path(trip_id, file_name)
        if path.exists():
            return  # immutable
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def list_files(self, trip_id: str) -> list[str]:
        files_dir = self.trip_dir(trip_id) / "files"
        if not files_dir.exists():
            return []
        return [f.stem for f in files_dir.iterdir() if f.is_file()]

    def _file_path(self, trip_id: str, file_name: str) -> Path:
        safe_name = file_name.replace("/", "_")
        return self.trip_dir(trip_id) / "files" / f"{safe_name}.dat"
