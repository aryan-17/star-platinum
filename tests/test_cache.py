"""Tests for local file cache."""

from pathlib import Path

import pytest

from oncall_rca.tools.cache import TripCache, CacheMiss


@pytest.fixture
def cache(tmp_path: Path) -> TripCache:
    return TripCache(tmp_path)


class TestTripCache:
    def test_write_and_read_trip_index(self, cache: TripCache) -> None:
        data = {"air_api_call": [], "header_data": [{"trip": ["123"]}]}
        cache.write_trip_index("123456789012", data)
        assert cache.has_trip_index("123456789012")
        result = cache.read_trip_index("123456789012")
        assert result == data

    def test_trip_index_immutable(self, cache: TripCache) -> None:
        cache.write_trip_index("123456789012", {"v": 1})
        cache.write_trip_index("123456789012", {"v": 2})  # should not overwrite
        result = cache.read_trip_index("123456789012")
        assert result == {"v": 1}

    def test_cache_miss_trip_index(self, cache: TripCache) -> None:
        with pytest.raises(CacheMiss):
            cache.read_trip_index("000000000000")

    def test_write_and_read_file(self, cache: TripCache) -> None:
        content = b"<soap:Envelope>test</soap:Envelope>"
        cache.write_file("123456789012", "SUPPLIER_BOOK-req.gz", content)
        assert cache.has_file("123456789012", "SUPPLIER_BOOK-req.gz")
        result = cache.read_file("123456789012", "SUPPLIER_BOOK-req.gz")
        assert result == content

    def test_file_immutable(self, cache: TripCache) -> None:
        cache.write_file("123456789012", "f.gz", b"original")
        cache.write_file("123456789012", "f.gz", b"overwrite")
        assert cache.read_file("123456789012", "f.gz") == b"original"

    def test_cache_miss_file(self, cache: TripCache) -> None:
        with pytest.raises(CacheMiss):
            cache.read_file("123456789012", "nonexistent.gz")

    def test_list_files(self, cache: TripCache) -> None:
        cache.write_file("123456789012", "a.gz", b"a")
        cache.write_file("123456789012", "b.gz", b"b")
        files = cache.list_files("123456789012")
        assert sorted(files) == ["a.gz", "b.gz"]

    def test_list_files_empty(self, cache: TripCache) -> None:
        assert cache.list_files("000000000000") == []

    def test_has_trip_index_false(self, cache: TripCache) -> None:
        assert not cache.has_trip_index("000000000000")
