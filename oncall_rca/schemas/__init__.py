"""Typed contracts between stages — v1.

Additive-only rule: after freeze, only new optional fields may be added.
A breaking change requires a new version alongside the old one.
"""

from oncall_rca.schemas.code_context import CodeContext, CodeFile
from oncall_rca.schemas.evidence import (
    BaggageChainStep,
    Citation,
    DivergencePoint,
    EvidenceFact,
    EvidencePack,
)
from oncall_rca.schemas.hypothesis import (
    Alternative,
    CitationCheck,
    Confidence,
    CriticReport,
    Hypothesis,
)
from oncall_rca.schemas.incident import Incident, IncidentType
from oncall_rca.schemas.rca_doc import RCADoc, TimelineEntry
from oncall_rca.schemas.run_state import RunState, StageRecord, StageStatus
from oncall_rca.schemas.trip_index import (
    CallNode,
    FileRef,
    Journey,
    PayloadFormat,
    Service,
    TripIndex,
)

__all__ = [
    "Alternative",
    "BaggageChainStep",
    "CallNode",
    "Citation",
    "CitationCheck",
    "CodeContext",
    "CodeFile",
    "Confidence",
    "CriticReport",
    "DivergencePoint",
    "EvidenceFact",
    "EvidencePack",
    "FileRef",
    "Hypothesis",
    "Incident",
    "IncidentType",
    "Journey",
    "PayloadFormat",
    "RCADoc",
    "RunState",
    "Service",
    "StageRecord",
    "StageStatus",
    "TimelineEntry",
    "TripIndex",
]
