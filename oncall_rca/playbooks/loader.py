"""Playbook engine — YAML-driven chain, divergence classes, red herrings.

Replaces hardcoded _API_TO_STEPS, _RED_HERRINGS, _DIVERGENCE_CLASSES, and
chain_order with YAML-loaded Pydantic models.  Adding a new incident type
or supplier is a new YAML file, not a code change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_PLAYBOOKS_DIR = Path(__file__).parent
_SUPPLIERS_DIR = _PLAYBOOKS_DIR / "suppliers"


class BenignError(BaseModel):
    """A supplier error code that is expected and should not be blamed."""

    code: str
    meaning: str


class SupplierConfig(BaseModel):
    """Supplier-specific configuration loaded from YAML."""

    name: str
    host_patterns: list[str] = Field(default_factory=list)
    benign_errors: list[BenignError] = Field(default_factory=list)
    inline_ticketing: bool = False


class Playbook(BaseModel):
    """Incident-type playbook loaded from YAML.

    Defines the comparison chain, API-to-step mapping, divergence classes,
    and red herrings for one incident type.
    """

    name: str
    incident_type: str
    chain: list[str] = Field(
        description="Ordered BaggageChainStep values for divergence walking",
    )
    api_to_steps: dict[str, list[str]] = Field(
        description="API name → list of BaggageChainStep values to extract",
    )
    divergence_classes: dict[str, str] = Field(
        description="BaggageChainStep value → human-readable divergence description",
    )
    red_herrings: list[str] = Field(
        default_factory=list,
        description="Known false positives to warn the Investigator about",
    )


def load_playbook(incident_type: str) -> Playbook:
    """Load a playbook YAML by incident type name.

    Args:
        incident_type: e.g. ``"baggage_mismatch"``.

    Returns:
        Validated Playbook model.

    Raises:
        FileNotFoundError: If no YAML file exists for the incident type.
        ValidationError: If the YAML is missing required fields.
    """
    path = _PLAYBOOKS_DIR / f"{incident_type}.yaml"
    return _load_playbook_from_path(path)


def _load_playbook_from_path(path: Path) -> Playbook:
    """Load and validate a playbook from an arbitrary YAML path."""
    if not path.exists():
        raise FileNotFoundError(f"Playbook not found: {path}")
    with open(path) as f:
        data: Any = yaml.safe_load(f)
    return Playbook.model_validate(data)


def load_supplier(supplier_name: str) -> SupplierConfig | None:
    """Load a supplier config YAML by name.

    Args:
        supplier_name: e.g. ``"air_arabia"``.

    Returns:
        SupplierConfig if the file exists, None otherwise.
    """
    path = _SUPPLIERS_DIR / f"{supplier_name}.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        data: Any = yaml.safe_load(f)
    return SupplierConfig.model_validate(data)
