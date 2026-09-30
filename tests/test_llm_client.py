"""Tests for LLM client — mock client only (no API key needed)."""

import pytest
from pydantic import BaseModel

from oncall_rca.llm.client import LLMError, MockLLMClient


class SimpleResponse(BaseModel):
    """Test response schema."""

    answer: str = "default"
    confidence: float = 0.5


class TestMockLLMClient:
    def test_default_response(self) -> None:
        client = MockLLMClient()
        result = client.generate(
            prompt="test",
            response_schema=SimpleResponse,
            stage="test_stage",
        )
        assert isinstance(result, SimpleResponse)
        assert result.answer == "default"

    def test_configured_response(self) -> None:
        client = MockLLMClient()
        expected = SimpleResponse(answer="configured", confidence=0.9)
        client.set_response("SimpleResponse", expected)

        result = client.generate(
            prompt="test",
            response_schema=SimpleResponse,
            stage="test_stage",
        )
        assert result.answer == "configured"
        assert result.confidence == 0.9

    def test_calls_recorded(self) -> None:
        client = MockLLMClient()
        client.generate(prompt="prompt1", response_schema=SimpleResponse, stage="s1")
        client.generate(prompt="prompt2", system="sys", response_schema=SimpleResponse, stage="s2")

        assert len(client.calls) == 2
        assert client.calls[0]["prompt"] == "prompt1"
        assert client.calls[1]["system"] == "sys"
        assert client.calls[1]["stage"] == "s2"

    def test_no_default_fails_gracefully(self) -> None:
        class RequiredFields(BaseModel):
            name: str  # no default — can't be auto-constructed

        client = MockLLMClient()
        with pytest.raises(LLMError, match="no response configured"):
            client.generate(prompt="test", response_schema=RequiredFields)
