"""Tests for evidence engine — integration test against golden fixture."""

import json
from pathlib import Path

import pytest

from oncall_rca.schemas.evidence import BaggageChainStep
from oncall_rca.stages.evidence.divergence import find_first_divergence
from oncall_rca.stages.evidence.engine import build_evidence_pack
from oncall_rca.stages.evidence.extractors.base import get_extractor
from oncall_rca.stages.evidence.extractors.supplier_book import SupplierBookExtractor
from oncall_rca.tools.cache import TripCache
from oncall_rca.tools.log_api import parse_payload


FIXTURE_DIR = Path("evals/golden_incidents/260802431929")


class TestExtractorRegistry:
    def test_hold_core_registered(self) -> None:
        ext = get_extractor(BaggageChainStep.HOLD_CORE_RESPONSE)
        assert ext is not None

    def test_supplier_book_registered(self) -> None:
        ext = get_extractor(BaggageChainStep.SUPPLIER_BOOK)
        assert ext is not None

    def test_book_response_registered(self) -> None:
        ext = get_extractor(BaggageChainStep.BOOK_RESPONSE)
        assert ext is not None


class TestSupplierBookExtractor:
    def test_extract_real_payload(self) -> None:
        path = FIXTURE_DIR / "payloads" / "NEW-SMS-SUPPLIER_BOOK-10.36.3.110-1785662214823-4582-res.gz.txt"
        if not path.exists():
            pytest.skip("fixture not available")

        content = path.read_bytes()
        parsed = parse_payload(content)
        ext = SupplierBookExtractor()
        facts = ext.extract(parsed, journey_index=0, source_file=path.name)

        # Should extract PNR, success, baggage, ticket, FBC
        labels = {f.label for f in facts}
        assert any("PNR" in l for l in labels)
        assert any("success" in l for l in labels)
        assert any("baggage" in l for l in labels)
        assert any("ticket" in l or "e-ticket" in l for l in labels)

        # Check specific values
        pnr_fact = next(f for f in facts if "PNR" in f.label)
        assert len(pnr_fact.value) > 0

        baggage_facts = [f for f in facts if "baggage" in f.label]
        assert len(baggage_facts) >= 1
        assert any("30 Kg 1 Piece" in f.value for f in baggage_facts)


class TestHoldCoreExtractor:
    def test_extract_real_payload(self) -> None:
        path = FIXTURE_DIR / "payloads" / "HOLD_CORE-HOLD_CORE-10.36.47.2-1785662097643-3650-res.gz.txt"
        if not path.exists():
            pytest.skip("fixture not available")

        content = path.read_bytes()
        parsed = parse_payload(content)
        ext = get_extractor(BaggageChainStep.HOLD_CORE_RESPONSE)
        assert ext is not None
        facts = ext.extract(parsed, journey_index=0, source_file=path.name)

        labels = {f.label for f in facts}
        assert any("hold status" in l.lower() for l in labels)
        assert any("hold ID" in l or "hold id" in l.lower() for l in labels)


class TestDivergenceFinder:
    def test_no_divergence_healthy_trip(self) -> None:
        """Negative control: healthy booking should find no divergence."""
        from oncall_rca.schemas.evidence import EvidenceFact, Citation

        # Simulate healthy facts
        facts = [
            EvidenceFact(
                fact_id="j0_ss1_bag",
                journey_index=0,
                step=BaggageChainStep.SS1_DISPLAYED,
                label="SS1 check-in baggage",
                value="30 Kg 1 Piece",
                citation=Citation(source_file="ss1.gz"),
            ),
            EvidenceFact(
                fact_id="j0_hold_status",
                journey_index=0,
                step=BaggageChainStep.HOLD_CORE_RESPONSE,
                label="HOLD_CORE hold status",
                value="HOLD_SUCCESS",
                citation=Citation(source_file="hold.gz"),
            ),
            EvidenceFact(
                fact_id="j0_book_status",
                journey_index=0,
                step=BaggageChainStep.BOOK_RESPONSE,
                label="BOOK booking status",
                value="CNF",
                citation=Citation(source_file="book.gz"),
            ),
            EvidenceFact(
                fact_id="j0_supplier_success",
                journey_index=0,
                step=BaggageChainStep.SUPPLIER_BOOK,
                label="SUPPLIER_BOOK success",
                value="true",
                citation=Citation(source_file="supplier.gz"),
            ),
        ]
        dp = find_first_divergence(facts, journey_index=0)
        assert dp is None

    def test_divergence_on_hold_failure(self) -> None:
        from oncall_rca.schemas.evidence import EvidenceFact, Citation

        facts = [
            EvidenceFact(
                fact_id="j0_ss1",
                journey_index=0,
                step=BaggageChainStep.SS1_DISPLAYED,
                label="SS1 baggage",
                value="2 pieces",
                citation=Citation(source_file="ss1.gz"),
            ),
            EvidenceFact(
                fact_id="j0_hold",
                journey_index=0,
                step=BaggageChainStep.HOLD_CORE_RESPONSE,
                label="HOLD_CORE hold status",
                value="HOLD_FAILED",
                citation=Citation(source_file="hold.gz"),
            ),
        ]
        dp = find_first_divergence(facts, journey_index=0)
        assert dp is not None
        assert dp.step == BaggageChainStep.HOLD_CORE_RESPONSE

    def test_divergence_on_supplier_error(self) -> None:
        from oncall_rca.schemas.evidence import EvidenceFact, Citation

        facts = [
            EvidenceFact(
                fact_id="j0_ss1",
                journey_index=0,
                step=BaggageChainStep.SS1_DISPLAYED,
                label="SS1 baggage",
                value="2 pieces",
                citation=Citation(source_file="ss1.gz"),
            ),
            EvidenceFact(
                fact_id="j0_supplier_err",
                journey_index=0,
                step=BaggageChainStep.SUPPLIER_BOOK,
                label="SUPPLIER_BOOK error",
                value="err.2-maxico.exposed.invalid.transaction.id",
                citation=Citation(source_file="supplier.gz"),
            ),
        ]
        dp = find_first_divergence(facts, journey_index=0)
        assert dp is not None
        assert dp.step == BaggageChainStep.SUPPLIER_BOOK


class TestEvidencePackIntegration:
    def test_all_facts_have_citations(self) -> None:
        """Every extracted fact must have a valid citation."""
        if not FIXTURE_DIR.exists():
            pytest.skip("fixture not available")

        raw = json.loads((FIXTURE_DIR / "trip_index.json").read_text())

        # Set up cache with payload files
        cache = TripCache(FIXTURE_DIR.parent.parent.parent / "cache_test_tmp")
        # Use a simple cache that reads from fixture payloads directly
        # For this test, we'll just verify the engine doesn't crash
        pack = build_evidence_pack("260802431929", raw["data"], cache)

        assert pack.trip_index.trip_id == "260802431929"
        assert len(pack.anomalies) > 0
        assert len(pack.red_herrings) > 0

        # All facts must have citations
        for fact in pack.facts:
            assert fact.citation is not None
            assert fact.citation.source_file != ""
