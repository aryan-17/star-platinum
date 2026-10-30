"""Tests for the playbook engine — YAML-driven chain, divergence classes, red herrings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from oncall_rca.playbooks.loader import (
    _load_playbook_from_path,
    load_playbook,
    load_supplier,
)
from oncall_rca.schemas.evidence import BaggageChainStep


def test_load_baggage_mismatch_playbook() -> None:
    pb = load_playbook("baggage_mismatch")
    assert pb.name == "baggage_mismatch"
    assert pb.incident_type == "baggage_mismatch"
    assert BaggageChainStep.SS1_DISPLAYED.value in pb.chain
    assert len(pb.api_to_steps) > 0
    assert "SINGLE_SOLUTION_SEARCH" in pb.api_to_steps
    assert len(pb.divergence_classes) > 0
    assert len(pb.red_herrings) > 0


def test_load_unknown_playbook_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_playbook("nonexistent_playbook")


def test_load_air_arabia_supplier() -> None:
    sc = load_supplier("air_arabia")
    assert sc is not None
    assert sc.name == "Air Arabia"
    assert len(sc.host_patterns) > 0


def test_load_unknown_supplier_returns_none() -> None:
    assert load_supplier("nonexistent_airline") is None


def test_playbook_api_to_steps_values_are_valid_chain_steps() -> None:
    pb = load_playbook("baggage_mismatch")
    valid = {s.value for s in BaggageChainStep}
    for steps in pb.api_to_steps.values():
        for s in steps:
            assert s in valid, f"{s} not a valid BaggageChainStep"


def test_playbook_divergence_classes_keys_are_valid() -> None:
    pb = load_playbook("baggage_mismatch")
    valid = {s.value for s in BaggageChainStep}
    for key in pb.divergence_classes:
        assert key in valid, f"{key} not a valid BaggageChainStep"


def test_invalid_yaml_raises_validation_error(tmp_path: Path) -> None:
    """Playbook YAML missing required fields must raise, not silently load."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("name: broken\n")
    with pytest.raises(ValidationError):
        _load_playbook_from_path(bad_yaml)
