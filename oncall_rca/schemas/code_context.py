"""Code context schema — output of the Code Context stage."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CodeFile(BaseModel):
    """A relevant source file from a service repo."""

    repo: str = Field(description="Repository name, e.g. 'supply-core-new'")
    path: str = Field(description="File path relative to repo root")
    functions: list[str] = Field(
        default_factory=list,
        description="Relevant function/method names within the file",
    )
    start_line: int | None = Field(default=None, description="Start of relevant range")
    end_line: int | None = Field(default=None, description="End of relevant range")


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
