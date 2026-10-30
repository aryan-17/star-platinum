"""Tests for confidence-based auto-routing."""

from oncall_rca.routing import RoutingDecision, RoutingSettings, route_by_confidence
from oncall_rca.schemas.hypothesis import Confidence, CriticReport


def _make_reports(confidence: Confidence) -> dict[int, CriticReport]:
    return {0: CriticReport(confidence=confidence)}


def test_high_confidence_routes_to_human_review() -> None:
    """Placeholder: HIGH maps to HUMAN_REVIEW (not AUTO_FIX until eval data exists)."""
    settings = RoutingSettings()
    result = route_by_confidence(_make_reports(Confidence.HIGH), settings)
    assert result == RoutingDecision.HUMAN_REVIEW


def test_medium_confidence_routes_to_human_review() -> None:
    settings = RoutingSettings()
    result = route_by_confidence(_make_reports(Confidence.MEDIUM), settings)
    assert result == RoutingDecision.HUMAN_REVIEW


def test_low_confidence_routes_to_escalate() -> None:
    settings = RoutingSettings()
    result = route_by_confidence(_make_reports(Confidence.LOW), settings)
    assert result == RoutingDecision.ESCALATE


def test_empty_reports_routes_to_escalate() -> None:
    """No critic reports must default to ESCALATE, not crash."""
    settings = RoutingSettings()
    result = route_by_confidence({}, settings)
    assert result == RoutingDecision.ESCALATE


def test_mixed_confidence_takes_worst() -> None:
    """Multiple journeys: worst confidence wins."""
    settings = RoutingSettings()
    reports = {
        0: CriticReport(confidence=Confidence.HIGH),
        1: CriticReport(confidence=Confidence.LOW),
    }
    result = route_by_confidence(reports, settings)
    assert result == RoutingDecision.ESCALATE


def test_custom_thresholds() -> None:
    settings = RoutingSettings(high_confidence_action="auto_fix")
    result = route_by_confidence(_make_reports(Confidence.HIGH), settings)
    assert result == RoutingDecision.AUTO_FIX
