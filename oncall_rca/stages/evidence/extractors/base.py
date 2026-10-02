"""Base extractor protocol and registry."""

from __future__ import annotations

from typing import Any, Protocol

from oncall_rca.schemas.evidence import BaggageChainStep, Citation, EvidenceFact


class Extractor(Protocol):
    """Protocol for payload extractors. One per API step."""

    step: BaggageChainStep

    def extract(
        self,
        content: Any,
        *,
        journey_index: int,
        source_file: str,
    ) -> list[EvidenceFact]:
        """Extract evidence facts from a parsed payload."""
        ...


# Registry: step → extractor class
_REGISTRY: dict[BaggageChainStep, type] = {}


def register_extractor(step: BaggageChainStep):
    """Decorator to register an extractor for a baggage chain step."""
    def decorator(cls: type) -> type:
        _REGISTRY[step] = cls
        return cls
    return decorator


def get_extractor(step: BaggageChainStep) -> Extractor | None:
    """Get the extractor for a given step, or None if not registered."""
    cls = _REGISTRY.get(step)
    if cls is None:
        return None
    return cls()
