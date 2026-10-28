"""Code context schema — output of the Code Context stage."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


def _coerce_line_number(v: Any) -> int | None:
    """Coerce LLM output to int or None. Handles strings like 'ninety'."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            return None
    return None


_LineNumber = Annotated[int | None, BeforeValidator(_coerce_line_number)]


class CodeFile(BaseModel):
    """A relevant source file from a service repo."""

    repo: str = Field(description="Repository name, e.g. 'supply-core-new'")
    path: str = Field(description="File path relative to repo root")
    functions: list[str] = Field(
        default_factory=list,
        description="Relevant function/method names within the file",
    )
    start_line: _LineNumber = Field(default=None, description="Start of relevant range")
    end_line: _LineNumber = Field(default=None, description="End of relevant range")


class CodeContext(BaseModel):
    """Code relevant to the divergent step.

    Produced by: Code Context stage.
    Consumed by: Investigator, RCA Writer.
    """

    files: list[CodeFile] = Field(default_factory=list)
    commit_shas: dict[str, str] = Field(
        default_factory=dict,
        description="Repo name → commit SHA analysed",
    )
    changed_after_incident: list[CodeFile] = Field(
        default_factory=list,
        description="Files that changed after the incident date — code may differ from what ran",
    )
