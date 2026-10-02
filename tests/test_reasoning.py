"""Tests for Investigator and Critic — using MockLLMClient."""

from oncall_rca.llm.client import MockLLMClient
from oncall_rca.schemas.code_context import CodeContext, CodeFile
from oncall_rca.schemas.evidence import (
    BaggageChainStep,
    Citation,
    DivergencePoint,
    EvidenceFact,
    EvidencePack,
)
from oncall_rca.schemas.hypothesis import (
    Confidence,
    CriticReport,
    Hypothesis,
)
from oncall_rca.schemas.trip_index import TripIndex
from oncall_rca.stages.critic.critic import critique
from oncall_rca.stages.investigator.investigator import investigate, _build_prompt


class TestInvestigatorPrompt:
    def test_prompt_includes_divergence(self) -> None:
        evidence = EvidencePack(
            trip_index=TripIndex(trip_id="123456789012"),
            divergence_point=DivergencePoint(
                journey_index=0,
                step=BaggageChainStep.SIS_HOLD_REQUEST,
                expected_value="2 pieces",
                actual_value="0 pieces",
                citation=Citation(source_file="hold-req.gz", field_path="fareDetails.baggage"),
                divergence_class="SIS-HOLD FBC/fare != SS1",
            ),
            supplier="air_arabia",
        )
        code = CodeContext(
            files=[CodeFile(repo="supply-core-new", path="src/hold/HoldMainWorkflow.java")],
            commit_shas={"supply-core-new": "abc123"},
        )

        prompt = _build_prompt(evidence, code)
        assert "SIS-HOLD" in prompt or "sis_hold" in prompt
        assert "2 pieces" in prompt
        assert "0 pieces" in prompt
        assert "air_arabia" in prompt
        assert "HoldMainWorkflow" in prompt

    def test_prompt_no_divergence(self) -> None:
        evidence = EvidencePack(
            trip_index=TripIndex(trip_id="123456789012"),
            divergence_point=None,
        )
        prompt = _build_prompt(evidence, CodeContext())
        assert "No divergence found" in prompt


class TestInvestigatorWithMock:
    def test_returns_hypothesis(self) -> None:
        mock = MockLLMClient()
        mock.set_response("Hypothesis", Hypothesis(
            claim="Baggage dropped in HoldMainWorkflow",
            explanation="The fare benefits from SS1 were not propagated.",
            proposed_fix="Add baggage field to hold request builder",
        ))

        evidence = EvidencePack(
            trip_index=TripIndex(trip_id="123456789012"),
            supplier="air_arabia",
        )
        result = investigate(evidence, CodeContext(), mock)

        assert result.claim == "Baggage dropped in HoldMainWorkflow"
        assert len(mock.calls) == 1
        assert mock.calls[0]["stage"] == "investigator"


class TestCriticWithMock:
    def test_returns_critic_report(self) -> None:
        mock = MockLLMClient()
        mock.set_response("CriticReport", CriticReport(
            all_citations_valid=True,
            confidence=Confidence.HIGH,
            rubric_scores={
                "citation_coverage": True,
                "alternatives_ruled_out": True,
                "timeline_consistent": True,
                "all_symptoms_explained": True,
                "reproduction_path_exists": False,
            },
        ))

        hypothesis = Hypothesis(
            claim="Test claim",
            explanation="Test explanation",
        )
        evidence = EvidencePack(
            trip_index=TripIndex(trip_id="123456789012"),
        )

        report = critique(hypothesis, evidence, mock)
        assert report.confidence == Confidence.HIGH
        assert report.all_citations_valid
        assert len(mock.calls) == 1
        assert mock.calls[0]["stage"] == "critic"

    def test_critic_gets_fresh_context(self) -> None:
        """Critic should not see Investigator's reasoning — only hypothesis + evidence."""
        mock = MockLLMClient()
        mock.set_response("CriticReport", CriticReport(confidence=Confidence.MEDIUM))

        # Run investigator first
        mock.set_response("Hypothesis", Hypothesis(claim="test", explanation="test explanation"))
        evidence = EvidencePack(trip_index=TripIndex(trip_id="123456789012"))
        hypothesis = investigate(evidence, CodeContext(), mock)

        # Run critic — should be a separate call, not sharing context
        mock._calls.clear()
        report = critique(hypothesis, evidence, mock)

        assert len(mock.calls) == 1  # critic made exactly one call
        # Critic's prompt should contain hypothesis but not Investigator's internal reasoning
        assert "test" in mock.calls[0]["prompt"]
