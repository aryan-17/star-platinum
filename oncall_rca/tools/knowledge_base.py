"""Knowledge base — SQLite store of past RCAs searchable by the Investigator.

Stores RCA summaries with trip metadata. Supports text search across
root causes and symptoms to find similar past incidents.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class KBEntry:
    """A knowledge base entry — summary of a past RCA."""

    trip_id: str
    incident_type: str
    supplier: str
    root_cause: str
    divergence_step: str
    confidence: str
    symptom: str
    fix_applied: str
    created_at: str
    journeys: str  # e.g. "COK→CAI, CAI→COK"


class KnowledgeBase:
    """SQLite-backed knowledge base for past RCA results.

    Used by the Investigator to find patterns from previous incidents.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS rca_entries (
                trip_id TEXT PRIMARY KEY,
                incident_type TEXT NOT NULL,
                supplier TEXT DEFAULT '',
                root_cause TEXT NOT NULL,
                divergence_step TEXT DEFAULT '',
                confidence TEXT DEFAULT 'low',
                symptom TEXT DEFAULT '',
                fix_applied TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                journeys TEXT DEFAULT ''
            )
        """)
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS rca_fts
            USING fts5(trip_id, root_cause, symptom, fix_applied, supplier,
                       content='rca_entries', content_rowid='rowid')
        """)
        self._conn.commit()

    def add(self, entry: KBEntry) -> None:
        """Add or update an RCA entry."""
        self._conn.execute("""
            INSERT OR REPLACE INTO rca_entries
            (trip_id, incident_type, supplier, root_cause, divergence_step,
             confidence, symptom, fix_applied, created_at, journeys)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.trip_id, entry.incident_type, entry.supplier,
            entry.root_cause, entry.divergence_step, entry.confidence,
            entry.symptom, entry.fix_applied, entry.created_at, entry.journeys,
        ))
        # Update FTS index
        self._conn.execute("""
            INSERT OR REPLACE INTO rca_fts
            (rowid, trip_id, root_cause, symptom, fix_applied, supplier)
            SELECT rowid, trip_id, root_cause, symptom, fix_applied, supplier
            FROM rca_entries WHERE trip_id = ?
        """, (entry.trip_id,))
        self._conn.commit()

    def search(self, query: str, *, max_results: int = 5) -> list[KBEntry]:
        """Full-text search across root causes, symptoms, and fixes.

        Args:
            query: Search terms (e.g. "baggage dropped hold request").
            max_results: Maximum entries to return.

        Returns:
            Matching KBEntry objects, ranked by relevance.
        """
        rows = self._conn.execute("""
            SELECT e.* FROM rca_entries e
            JOIN rca_fts f ON e.rowid = f.rowid
            WHERE rca_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, max_results)).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get(self, trip_id: str) -> KBEntry | None:
        """Get a specific entry by trip ID."""
        row = self._conn.execute(
            "SELECT * FROM rca_entries WHERE trip_id = ?", (trip_id,)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def list_recent(self, *, limit: int = 20) -> list[KBEntry]:
        """List recent entries."""
        rows = self._conn.execute(
            "SELECT * FROM rca_entries ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM rca_entries").fetchone()[0]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> KBEntry:
        return KBEntry(
            trip_id=row["trip_id"],
            incident_type=row["incident_type"],
            supplier=row["supplier"],
            root_cause=row["root_cause"],
            divergence_step=row["divergence_step"],
            confidence=row["confidence"],
            symptom=row["symptom"],
            fix_applied=row["fix_applied"],
            created_at=row["created_at"],
            journeys=row["journeys"],
        )
