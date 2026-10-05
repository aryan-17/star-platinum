"""Playbook generator — LLM generates playbook YAML from incident description.

Given a new incident type, generates the playbook structure:
- Ordered API chain
- Per step: file to open, payload format, field path
- Invariants that must hold
- Known quirks / red herrings
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from oncall_rca.llm.client import LLMClient


class PlaybookStep(BaseModel):
    """A single step in a playbook chain."""

    step_name: str = Field(description="Human-readable step name")
    api_name: str = Field(description="API name as it appears in logs")
    check_file: str = Field(description="'req' or 'res'")
    payload_format: str = Field(description="'json' or 'soap_xml'")
    field_path: str = Field(description="JSON path or XPath to the value to check")
    what_to_check: str = Field(description="What this step verifies")
    invariant: str = Field(description="What must be true at this step")


class GeneratedPlaybook(BaseModel):
    """LLM-generated playbook for a new incident type."""

    incident_type: str = Field(description="e.g. 'fare_mismatch', 'booking_failure'")
    description: str = Field(description="What this playbook detects")
    chain: list[PlaybookStep] = Field(default_factory=list)
    red_herrings: list[str] = Field(default_factory=list)
    notes: str = Field(default="")


_SYSTEM_PROMPT = """You are an expert in flight booking systems. You generate playbook definitions
for the RCA agent to follow when investigating incidents.

A playbook defines:
1. An ordered chain of API steps to trace a value through the booking flow
2. At each step: which API's req/res to check, the field path, and what the invariant is
3. Known red herrings that should not be blamed

The booking flow has these services:
- supply-core-new: what the user was shown (SS1 search, SIS-HOLD request)
- air-sms / air-sms-new: what was actually booked (HOLD_CORE, BOOK, SUPPLIER_BOOK)

API logs available: SS1 (gRPC JSON), SIS-HOLD (JSON), HOLD_CORE (JSON), SMS_HOLD (JSON),
SMS_BOOK (JSON), SUPPLIER_BOOK (SOAP XML), SUPPLIER_PRICE_QUOTE (SOAP XML),
SUPPLIER_ANCILLARY_BAGGAGE (SOAP XML), BOOK (JSON)

Respond with JSON matching the GeneratedPlaybook schema."""


def generate_playbook(
    incident_description: str,
    llm: LLMClient,
) -> GeneratedPlaybook:
    """Generate a playbook for a new incident type.

    Args:
        incident_description: Natural language description of the incident type.
            e.g. "Fare shown to user doesn't match what was charged after booking"
        llm: LLM client for generation.

    Returns:
        GeneratedPlaybook with chain, invariants, and red herrings.
    """
    prompt = (
        f"Generate a playbook for investigating this incident type:\n\n"
        f"{incident_description}\n\n"
        f"Define the chain of API steps to trace, what to check at each step, "
        f"and what invariants must hold."
    )

    return llm.generate(
        prompt=prompt,
        system=_SYSTEM_PROMPT,
        response_schema=GeneratedPlaybook,
        stage="playbook_generator",
        temperature=0.0,
    )


def playbook_to_yaml(playbook: GeneratedPlaybook) -> str:
    """Convert a GeneratedPlaybook to YAML format for saving."""
    import yaml

    data = {
        "incident_type": playbook.incident_type,
        "description": playbook.description,
        "chain": [
            {
                "step": s.step_name,
                "api": s.api_name,
                "check": s.check_file,
                "format": s.payload_format,
                "field_path": s.field_path,
                "what_to_check": s.what_to_check,
                "invariant": s.invariant,
            }
            for s in playbook.chain
        ],
        "red_herrings": playbook.red_herrings,
        "notes": playbook.notes,
    }
    return yaml.dump(data, default_flow_style=False, sort_keys=False)
