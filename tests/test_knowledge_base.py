"""Tests for knowledge base."""

from pathlib import Path

import pytest

from oncall_rca.tools.knowledge_base import KBEntry, KnowledgeBase


@pytest.fixture
def kb(tmp_path: Path) -> KnowledgeBase:
    db = KnowledgeBase(tmp_path / "state" / "kb.db")
    yield db
    db.close()


@pytest.fixture
def sample_entry() -> KBEntry:
    return KBEntry(
        trip_id="260802431929",
        incident_type="baggage_mismatch",
        supplier="air_arabia",
        root_cause="Baggage dropped in HoldMainWorkflow — fare benefits not propagated to hold request",
        divergence_step="sis_hold_request",
        confidence="high",
        symptom="User shown 2 pieces but none assigned after booking",
        fix_applied="Added baggage field propagation in HoldMainWorkflow.buildHoldRequest()",
        created_at="2026-10-01",
        journeys="COK→CAI, CAI→COK",
    )


class TestKnowledgeBase:
    def test_add_and_get(self, kb: KnowledgeBase, sample_entry: KBEntry) -> None:
        kb.add(sample_entry)
        result = kb.get("260802431929")
        assert result is not None
        assert result.trip_id == "260802431929"
        assert result.supplier == "air_arabia"
        assert "HoldMainWorkflow" in result.root_cause

    def test_count(self, kb: KnowledgeBase, sample_entry: KBEntry) -> None:
        assert kb.count() == 0
        kb.add(sample_entry)
        assert kb.count() == 1

    def test_search_by_root_cause(self, kb: KnowledgeBase, sample_entry: KBEntry) -> None:
        kb.add(sample_entry)
        results = kb.search("baggage dropped hold")
        assert len(results) == 1
        assert results[0].trip_id == "260802431929"

    def test_search_by_symptom(self, kb: KnowledgeBase, sample_entry: KBEntry) -> None:
        kb.add(sample_entry)
        results = kb.search("2 pieces none assigned")
        assert len(results) == 1

    def test_search_by_supplier(self, kb: KnowledgeBase, sample_entry: KBEntry) -> None:
        kb.add(sample_entry)
        results = kb.search("air_arabia")
        assert len(results) == 1

    def test_search_no_match(self, kb: KnowledgeBase, sample_entry: KBEntry) -> None:
        kb.add(sample_entry)
        results = kb.search("flydubai seat selection")
        assert len(results) == 0

    def test_list_recent(self, kb: KnowledgeBase) -> None:
        for i in range(3):
            kb.add(KBEntry(
                trip_id=f"00000000000{i}",
                incident_type="baggage_mismatch",
                supplier="air_arabia",
                root_cause=f"Root cause {i}",
                divergence_step="",
                confidence="medium",
                symptom=f"Symptom {i}",
                fix_applied="",
                created_at=f"2026-10-0{i+1}",
                journeys="",
            ))
        recent = kb.list_recent(limit=2)
        assert len(recent) == 2
        assert recent[0].created_at == "2026-10-03"

    def test_upsert(self, kb: KnowledgeBase, sample_entry: KBEntry) -> None:
        kb.add(sample_entry)
        updated = KBEntry(
            **{**sample_entry.__dict__, "fix_applied": "Updated fix"}
        )
        kb.add(updated)
        assert kb.count() == 1
        result = kb.get("260802431929")
        assert result is not None
        assert result.fix_applied == "Updated fix"

    def test_get_missing(self, kb: KnowledgeBase) -> None:
        assert kb.get("000000000000") is None
