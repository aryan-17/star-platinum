"""LLM client wrapper — interface + Gemini Flash implementation.

Design principles (§6.1, §12):
- Every LLM call returns schema-validated JSON (structured output).
- Single client with retries, timeouts, token accounting.
- Behind a Protocol so it can be swapped or mocked.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from oncall_rca.observability.trace import TraceRecord, trace_store

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    """Protocol for LLM clients. All stages depend on this, not the concrete impl."""

    def generate(
        self,
        *,
        prompt: str,
        system: str = "",
        response_schema: type[T],
        stage: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> T:
        """Generate a structured response validated against response_schema.

        Args:
            prompt: User prompt content.
            system: System prompt (wraps untrusted data as data, not instructions).
            response_schema: Pydantic model class to validate output against.
            stage: Stage name for observability.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.

        Returns:
            Validated instance of response_schema.

        Raises:
            LLMError: On API failure after retries.
            LLMValidationError: If output cannot be parsed into response_schema.
            LLMBudgetExceeded: If token budget is exceeded.
        """
        ...


class LLMError(Exception):
    """Base error for LLM operations."""


class LLMValidationError(LLMError):
    """LLM output failed schema validation."""


class LLMBudgetExceeded(LLMError):
    """Token or cost budget exceeded."""


class GeminiFlashClient:
    """Gemini Flash client with retries, timeouts, token accounting.

    Requires GEMINI_API_KEY to be set. Constructed via from_settings().
    """

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
        max_retries: int = 3,
        timeout_seconds: int = 60,
        token_budget: int = 0,
    ) -> None:
        if not api_key:
            raise LLMError("GEMINI_API_KEY is required")
        self._api_key = api_key
        self._model_name = model_name
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._token_budget = token_budget
        self._tokens_used = 0

    @classmethod
    def from_settings(cls, settings: Any) -> GeminiFlashClient:
        """Create from application Settings."""
        return cls(
            api_key=settings.model.api_key,
            model_name=settings.model.model,
            token_budget=settings.budgets.tokens_per_stage_limit,
        )

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    def generate(
        self,
        *,
        prompt: str,
        system: str = "",
        response_schema: type[T],
        stage: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> T:
        """Generate structured output from Gemini Flash.

        Uses google.generativeai for the API call.
        Retries on transient errors. Validates output against response_schema.
        """
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(self._model_name)

        trace_id = str(uuid.uuid4())[:8]
        start = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                full_prompt = f"{system}\n\n{prompt}" if system else prompt
                response = model.generate_content(
                    full_prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    ),
                )

                elapsed_ms = int((time.monotonic() - start) * 1000)

                # Token accounting
                input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0)
                output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0)
                total_tokens = input_tokens + output_tokens
                self._tokens_used += total_tokens

                if self._token_budget and self._tokens_used > self._token_budget:
                    raise LLMBudgetExceeded(
                        f"Token budget exceeded: {self._tokens_used}/{self._token_budget}"
                    )

                # Parse and validate
                raw_text = response.text
                result = response_schema.model_validate_json(raw_text)

                # Trace
                trace_store.record(TraceRecord(
                    trace_id=trace_id,
                    stage=stage,
                    operation="llm_generate",
                    model=self._model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=elapsed_ms,
                    success=True,
                ))

                return result

            except LLMBudgetExceeded:
                raise
            except Exception as e:
                last_error = e
                if attempt < self._max_retries:
                    time.sleep(min(2**attempt, 10))
                    continue

                elapsed_ms = int((time.monotonic() - start) * 1000)
                trace_store.record(TraceRecord(
                    trace_id=trace_id,
                    stage=stage,
                    operation="llm_generate",
                    model=self._model_name,
                    latency_ms=elapsed_ms,
                    success=False,
                    error=str(e),
                ))
                raise LLMError(f"LLM call failed after {self._max_retries} attempts: {e}") from e

        raise LLMError(f"LLM call failed: {last_error}")


class MockLLMClient:
    """Mock LLM client for testing without API keys.

    Returns a default instance of the response_schema.
    """

    def __init__(self) -> None:
        self._calls: list[dict[str, Any]] = []
        self._responses: dict[str, Any] = {}

    def set_response(self, schema_name: str, response: BaseModel) -> None:
        """Pre-configure a response for a given schema type."""
        self._responses[schema_name] = response

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._calls

    def generate(
        self,
        *,
        prompt: str,
        system: str = "",
        response_schema: type[T],
        stage: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> T:
        self._calls.append({
            "prompt": prompt,
            "system": system,
            "schema": response_schema.__name__,
            "stage": stage,
        })

        schema_name = response_schema.__name__
        if schema_name in self._responses:
            return self._responses[schema_name]  # type: ignore[return-value]

        # Return default instance (all fields must have defaults or be Optional)
        try:
            return response_schema()  # type: ignore[call-arg]
        except Exception:
            raise LLMError(
                f"MockLLMClient: no response configured for {schema_name} "
                f"and default construction failed. Use set_response()."
            )
