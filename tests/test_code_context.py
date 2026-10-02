"""Tests for code tools and code context builder."""

import os
import subprocess
from pathlib import Path

import pytest

from oncall_rca.stages.code_context.builder import _find_service, load_service_map
from oncall_rca.tools.code_tools import code_grep, code_read, code_list, git_log, GrepResult


class TestServiceMap:
    @pytest.fixture
    def service_map(self):
        return load_service_map()

    def test_loads(self, service_map) -> None:
        assert "services" in service_map
        assert "supply-core-new" in service_map["services"]
        assert "air-sms" in service_map["services"]
        assert "air-sms-new" in service_map["services"]

    def test_find_supplier_book(self, service_map) -> None:
        repo, entry_points = _find_service("SUPPLIER_BOOK", "NEW-SMS", service_map)
        assert repo == "air-sms-new"
        assert "SupplierBookAdapter" in entry_points

    def test_find_sms_hold(self, service_map) -> None:
        repo, entry_points = _find_service("SMS_HOLD", "NEW-SMS", service_map)
        assert repo == "air-sms-new"
        assert "HoldFlightController" in entry_points

    def test_find_hold_core(self, service_map) -> None:
        repo, entry_points = _find_service("HOLD_CORE", "HOLD_CORE", service_map)
        assert repo == "air-sms"
        assert "HoldCoreController" in entry_points

    def test_find_supply_core(self, service_map) -> None:
        repo, _ = _find_service("MINI_RULES", "SUPPLY_CORE_ORCHESTRATOR", service_map)
        assert repo == "supply-core-new"

    def test_unknown_api(self, service_map) -> None:
        repo, entry_points = _find_service("UNKNOWN_API", "UNKNOWN_TYPE", service_map)
        assert repo == ""
        assert entry_points == []


class TestCodeTools:
    """Test code tools against this repo itself."""

    @pytest.fixture
    def repo_path(self) -> Path:
        return Path.cwd()

    def test_code_grep(self, repo_path: Path) -> None:
        results = code_grep(repo_path, "class TripIndex", max_results=5)
        assert len(results) >= 1
        assert any("trip_index.py" in r.file for r in results)

    def test_code_grep_no_match(self, repo_path: Path) -> None:
        results = code_grep(repo_path / "oncall_rca", "zQxWpLm_no_match_ever_99")
        assert len(results) == 0

    def test_code_read(self, repo_path: Path) -> None:
        result = code_read(repo_path, "oncall_rca/schemas/incident.py", start_line=1, end_line=10)
        assert result.total_lines > 5
        assert "incident" in result.content.lower() or "import" in result.content.lower()

    def test_code_read_bounded(self, repo_path: Path) -> None:
        result = code_read(repo_path, "oncall_rca/schemas/incident.py", max_lines=5)
        lines = result.content.strip().splitlines()
        assert len(lines) <= 5

    def test_code_list(self, repo_path: Path) -> None:
        files = code_list(repo_path, "oncall_rca/schemas/*.py")
        assert len(files) >= 5
        assert any("incident.py" in f for f in files)

    def test_git_log(self, repo_path: Path) -> None:
        log = git_log(repo_path, max_entries=5)
        assert len(log) > 0
