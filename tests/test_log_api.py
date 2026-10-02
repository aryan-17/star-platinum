"""Tests for log API clients — using cached fixture data."""

import json
from pathlib import Path

import pytest

from oncall_rca.tools.log_api import detect_format, parse_payload, fetch_trip_index, TripNotFound
from oncall_rca.config.settings import Settings


FIXTURE_DIR = Path("evals/golden_incidents/260802431929")


class TestDetectFormat:
    def test_json(self) -> None:
        assert detect_format(b'{"key": "value"}') == "json"
        assert detect_format(b'  [1, 2, 3]') == "json"

    def test_soap_xml(self) -> None:
        assert detect_format(b'<soap:Envelope>') == "soap_xml"
        assert detect_format(b'  <?xml version="1.0"?>') == "soap_xml"

    def test_plain_text(self) -> None:
        assert detect_format(b"some log line") == "plain_text"
        assert detect_format(b"200 OK") == "plain_text"


class TestParsePayload:
    def test_parse_json(self) -> None:
        result = parse_payload(b'{"status": "ok"}')
        assert result == {"status": "ok"}

    def test_parse_soap_xml(self) -> None:
        xml = b'<root><child attr="1"/></root>'
        result = parse_payload(xml)
        assert result.tag == "root"
        assert result[0].get("attr") == "1"

    def test_parse_plain_text(self) -> None:
        result = parse_payload(b"just a log line")
        assert result == "just a log line"


class TestParseRealPayloads:
    """Parse actual cached payloads from the golden fixture."""

    def test_parse_supplier_book_soap(self) -> None:
        path = FIXTURE_DIR / "payloads" / "NEW-SMS-SUPPLIER_BOOK-10.36.3.110-1785662214823-4582-res.gz.txt"
        if not path.exists():
            pytest.skip("fixture not available")
        content = path.read_bytes()
        assert detect_format(content) == "soap_xml"
        el = parse_payload(content)
        # Should have parsed without error; check it's an XML element
        assert el.tag is not None

    def test_parse_hold_core_json(self) -> None:
        path = FIXTURE_DIR / "payloads" / "HOLD_CORE-HOLD_CORE-10.36.47.2-1785662097643-3650-res.gz.txt"
        if not path.exists():
            pytest.skip("fixture not available")
        content = path.read_bytes()
        fmt = detect_format(content)
        assert fmt == "json"
        result = parse_payload(content)
        assert isinstance(result, dict)


class TestTripIndex:
    def test_load_cached_trip_index(self) -> None:
        """Verify the cached fixture loads and has expected structure."""
        path = FIXTURE_DIR / "trip_index.json"
        if not path.exists():
            pytest.skip("fixture not available")
        data = json.loads(path.read_text())["data"]
        assert "air_api_call" in data
        assert "air_book" in data
        assert "header_data" in data
        assert "files_list" in data
        assert len(data["air_api_call"]) > 100
