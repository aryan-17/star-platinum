"""LLM client wrapper — interface + Groq implementation.

Design principles (§6.1, §12):
- Every LLM call returns schema-validated JSON (structured output).
- Single client with retries, timeouts, token accounting.
- Behind a Protocol so it can be swapped or mocked.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

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

    def generate_with_tools(
        self,
        *,
        system: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any] | None], str],
        response_schema: type[T],
        stage: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_iterations: int = 10,
    ) -> T:
        """Run a ReAct tool-calling loop and return a structured result.

        The LLM receives tools and decides which to call. Tool results are
        fed back as messages. Loop ends when the LLM produces a final
        answer (no tool calls) or max_iterations is reached.

        Args:
            system: System prompt (instructions only, no data).
            user_prompt: Initial user message (seed context).
            tools: Groq/OpenAI function-calling tool definitions.
            tool_executor: Callable that executes a tool by name, returns result string.
            response_schema: Pydantic model class to validate the final answer.
            stage: Stage name for observability.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens per turn.
            max_iterations: Maximum tool-calling rounds.

        Returns:
            Validated instance of response_schema.
        """
        ...


class LLMError(Exception):
    """Base error for LLM operations."""


class LLMValidationError(LLMError):
    """LLM output failed schema validation."""


class LLMBudgetExceeded(LLMError):
    """Token or cost budget exceeded."""


class GroqClient:
    """Groq client with retries, timeouts, token accounting.

    Uses the Groq Python SDK (OpenAI-compatible chat completions).
    Requires GROQ_API_KEY to be set.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = "openai/gpt-oss-120b",
        max_retries: int = 3,
        timeout_seconds: int = 60,
        token_budget: int = 0,
        stage_models: dict[str, str] | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("GROQ_API_KEY is required")
        self._api_key = api_key
        self._model_name = model_name
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._token_budget = token_budget
        self._tokens_used = 0
        self._stage_models: dict[str, str] = stage_models or {}

    def _resolve_model(self, stage: str) -> str:
        """Return the model for a stage, falling back to default."""
        return self._stage_models.get(stage, self._model_name)

    @classmethod
    def from_settings(cls, settings: Any) -> GroqClient:
        """Create from application Settings."""
        return cls(
            api_key=settings.model.api_key,
            model_name=settings.model.model,
            token_budget=settings.budgets.tokens_per_stage_limit,
            stage_models=getattr(settings.model, "stage_models", None),
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
        """Generate structured output from Groq.

        Uses groq SDK for the API call.
        Retries on transient errors. Validates output against response_schema.
        """
        from groq import Groq

        client = Groq(api_key=self._api_key)

        trace_id = str(uuid.uuid4())[:8]
        start = time.monotonic()
        last_error: Exception | None = None

        schema_json = response_schema.model_json_schema()
        schema_instruction = (
            f"You MUST respond with valid JSON matching this schema:\n"
            f"```json\n{json.dumps(schema_json, indent=2)}\n```\n"
            f"Respond ONLY with the JSON object. No other text."
        )

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": f"{system}\n\n{schema_instruction}"})
        else:
            messages.append({"role": "system", "content": schema_instruction})
        messages.append({"role": "user", "content": prompt})

        effective_model = self._resolve_model(stage)

        for attempt in range(1, self._max_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=effective_model,
                    messages=messages,
                    temperature=temperature,
                    max_completion_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )

                elapsed_ms = int((time.monotonic() - start) * 1000)

                # Token accounting
                usage = response.usage
                input_tokens = usage.prompt_tokens if usage else 0
                output_tokens = usage.completion_tokens if usage else 0
                total_tokens = input_tokens + output_tokens
                self._tokens_used += total_tokens

                if self._token_budget and self._tokens_used > self._token_budget:
                    raise LLMBudgetExceeded(
                        f"Token budget exceeded: {self._tokens_used}/{self._token_budget}"
                    )

                # Parse and validate
                raw_text = response.choices[0].message.content or ""
                try:
                    result = response_schema.model_validate_json(raw_text)
                except Exception as e:
                    raise LLMValidationError(
                        f"Failed to validate LLM output against {response_schema.__name__}: {e}\n"
                        f"Raw output: {raw_text[:500]}"
                    ) from e

                # Trace
                trace_store.record(TraceRecord(
                    trace_id=trace_id,
                    stage=stage,
                    operation="llm_generate",
                    model=effective_model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=elapsed_ms,
                    success=True,
                ))

                return result

            except (LLMBudgetExceeded, LLMValidationError):
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
                    model=effective_model,
                    latency_ms=elapsed_ms,
                    success=False,
                    error=str(e),
                ))
                raise LLMError(f"LLM call failed after {self._max_retries} attempts: {e}") from e

        raise LLMError(f"LLM call failed: {last_error}")

    def generate_with_tools(
        self,
        *,
        system: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any] | None], str],
        response_schema: type[T],
        stage: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_iterations: int = 10,
    ) -> T:
        """Run a ReAct tool-calling loop via Groq function calling."""
        from groq import Groq

        client = Groq(api_key=self._api_key)
        trace_id = str(uuid.uuid4())[:8]
        start = time.monotonic()
        effective_model = self._resolve_model(stage)

        schema_json = response_schema.model_json_schema()
        schema_instruction = (
            "\n\nWhen you have gathered enough evidence, respond with ONLY "
            "a JSON object matching this schema (no tool calls):\n"
            f"```json\n{json.dumps(schema_json, indent=2)}\n```"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system + schema_instruction},
            {"role": "user", "content": user_prompt},
        ]

        for iteration in range(max_iterations):
            try:
                # On the last iteration, drop tools to force a final answer
                call_kwargs: dict[str, Any] = {
                    "model": effective_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_completion_tokens": max_tokens,
                }
                if iteration < max_iterations - 1:
                    call_kwargs["tools"] = tools
                    call_kwargs["tool_choice"] = "auto"
                else:
                    call_kwargs["response_format"] = {"type": "json_object"}

                response = client.chat.completions.create(**call_kwargs)

                # Token accounting
                usage = response.usage
                input_tokens = usage.prompt_tokens if usage else 0
                output_tokens = usage.completion_tokens if usage else 0
                self._tokens_used += input_tokens + output_tokens

                if self._token_budget and self._tokens_used > self._token_budget:
                    raise LLMBudgetExceeded(
                        f"Token budget exceeded: {self._tokens_used}/{self._token_budget}"
                    )

                choice = response.choices[0]
                assistant_msg = choice.message

                # If LLM called tools, execute and loop
                if assistant_msg.tool_calls:
                    # Append assistant message with tool calls
                    messages.append({
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in assistant_msg.tool_calls
                        ],
                    })

                    # Execute each tool and append results
                    for tc in assistant_msg.tool_calls:
                        try:
                            raw_args = tc.function.arguments
                            args = json.loads(raw_args) if raw_args else None
                        except json.JSONDecodeError:
                            args = None
                        result = tool_executor(tc.function.name, args)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })

                    trace_store.record(TraceRecord(
                        trace_id=trace_id,
                        stage=stage,
                        operation="tool_call",
                        model=effective_model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        success=True,
                        metadata={
                            "iteration": str(iteration),
                            "tools_called": ",".join(
                                tc.function.name for tc in assistant_msg.tool_calls
                            ),
                        },
                    ))
                    continue

                # No tool calls → final answer
                raw_text = assistant_msg.content or ""
                try:
                    result = response_schema.model_validate_json(raw_text)
                except Exception as e:
                    raise LLMValidationError(
                        f"Failed to validate tool-calling final answer against "
                        f"{response_schema.__name__}: {e}\n"
                        f"Raw output: {raw_text[:500]}"
                    ) from e

                elapsed_ms = int((time.monotonic() - start) * 1000)
                trace_store.record(TraceRecord(
                    trace_id=trace_id,
                    stage=stage,
                    operation="tool_call_complete",
                    model=effective_model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=elapsed_ms,
                    success=True,
                    metadata={"iterations": str(iteration + 1)},
                ))

                return result

            except (LLMBudgetExceeded, LLMValidationError):
                raise
            except Exception as e:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                trace_store.record(TraceRecord(
                    trace_id=trace_id,
                    stage=stage,
                    operation="tool_call",
                    model=effective_model,
                    latency_ms=elapsed_ms,
                    success=False,
                    error=str(e),
                ))
                raise LLMError(
                    f"Tool-calling loop failed at iteration {iteration}: {e}"
                ) from e

        raise LLMError(
            f"Tool-calling loop exhausted {max_iterations} iterations"
        )


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

    def generate_with_tools(
        self,
        *,
        system: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any] | None], str],
        response_schema: type[T],
        stage: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_iterations: int = 10,
    ) -> T:
        """Mock tool-calling: skip tools, return pre-configured response."""
        self._calls.append({
            "prompt": user_prompt,
            "system": system,
            "schema": response_schema.__name__,
            "stage": stage,
            "tools": [t["function"]["name"] for t in tools],
        })
        return self.generate(
            prompt=user_prompt,
            system=system,
            response_schema=response_schema,
            stage=stage,
        )
