"""Investigator tools — one tool per major evidence step.

Each tool returns only the specific extracted facts the LLM needs for that
step. No raw request/response payloads reach the LLM. Tools return
pre-formatted strings optimised for LLM consumption.

Tool definitions are Groq/OpenAI function-calling compatible JSON schemas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from oncall_rca.schemas.evidence import BaggageChainStep, EvidenceFact, EvidencePack

if TYPE_CHECKING:
    from oncall_rca.schemas.code_context import CodeContext


def _format_facts(facts: list[EvidenceFact], *, include_citation: bool = True) -> str:
    """Format a list of EvidenceFacts into a compact LLM-readable string."""
    if not facts:
        return "No facts found for this step."

    lines: list[str] = []
    for f in facts:
        line = f"- {f.label}: {f.value}"
        if include_citation:
            line += f"  [source: {f.citation.source_file} @ {f.citation.field_path}]"
        lines.append(line)
    return "\n".join(lines)


def _filter_facts(
    facts: list[EvidenceFact],
    *,
    journey_index: int,
    steps: set[BaggageChainStep],
) -> list[EvidenceFact]:
    """Filter facts by journey and step."""
    return [
        f for f in facts
        if f.journey_index == journey_index and f.step in steps
    ]


class InvestigatorToolkit:
    """Wraps EvidencePack + CodeContext, exposes per-step tools.

    Each method is a tool that returns a focused slice of evidence.
    ``to_tool_definitions()`` returns Groq/OpenAI function schemas.
    ``execute(name, args)`` dispatches a tool call to the right method.
    """

    def __init__(
        self,
        evidence: EvidencePack,
        journey_index: int,
        code_context: CodeContext,
    ) -> None:
        self._evidence = evidence
        self._journey_index = journey_index
        self._code_context = code_context

        self._journey_facts = evidence.facts_by_journey.get(journey_index, [])

        journey = next(
            (j for j in evidence.trip_index.journeys if j.journey_index == journey_index),
            None,
        )
        self._route = (
            f"{journey.origin} → {journey.destination}" if journey and journey.origin
            else f"journey {journey_index}"
        )
        self._supplier = journey.supplier if journey else evidence.supplier

        # Tool dispatch table
        self._dispatch: dict[str, Any] = {
            "get_fare_chain": self.get_fare_chain,
            "get_ss1_facts": self.get_ss1_facts,
            "get_sis_hold_facts": self.get_sis_hold_facts,
            "get_supplier_hold_facts": self.get_supplier_hold_facts,
            "get_hold_core_facts": self.get_hold_core_facts,
            "get_book_facts": self.get_book_facts,
            "get_supplier_book_facts": self.get_supplier_book_facts,
            "get_divergence_point": self.get_divergence_point,
            "get_anomalies": self.get_anomalies,
            "get_code_context": self.get_code_context,
        }

    # --- Tools ---

    def get_fare_chain(self) -> str:
        """Fare comparison across all steps: SS1 → SIS-HOLD → SUPPLIER_HOLD → BOOK.

        Shows fareId, comboFbc, baggageFareType at each step so you can see
        exactly where the fare switches. Call this FIRST.
        """
        chain_order: list[tuple[str, set[BaggageChainStep]]] = [
            ("SS1 (displayed)", {BaggageChainStep.SS1_DISPLAYED}),
            ("SIS-HOLD (held)", {BaggageChainStep.SIS_HOLD_REQUEST}),
            ("SUPPLIER_HOLD (supplier)", {
                BaggageChainStep.SUPPLIER_BAGGAGE_HOLD,
                BaggageChainStep.SUPPLIER_PRICE_HOLD,
            }),
            ("HOLD_CORE", {BaggageChainStep.HOLD_CORE_RESPONSE}),
            ("SUPPLIER_BOOK (booked)", {BaggageChainStep.SUPPLIER_BOOK}),
            ("BOOK (final)", {BaggageChainStep.BOOK_RESPONSE}),
        ]

        lines: list[str] = [
            f"Fare chain for {self._route} (supplier: {self._supplier}):",
        ]

        fare_keywords = {
            "fareid", "faretype", "baggagefaretype", "baggage benefit",
            "baggage (", "status", "booking status", "hold status",
            "fare basis", "combo", "fare type", "total fare",
        }

        for label, steps in chain_order:
            step_facts = _filter_facts(
                self._journey_facts, journey_index=self._journey_index, steps=steps,
            )
            # Only show fare-relevant facts in the chain view
            fare_facts = [
                f for f in step_facts
                if any(kw in f.label.lower() for kw in fare_keywords)
            ]
            if fare_facts:
                lines.append(f"\n{label}:")
                for f in fare_facts:
                    lines.append(
                        f"  {f.label} = {f.value}"
                        f"  [source: {f.citation.source_file}]"
                    )
            else:
                lines.append(f"\n{label}: (no fare data extracted)")

        return "\n".join(lines)

    def get_ss1_facts(self) -> str:
        """SS1 search results — what the user was shown (displayed world).

        Returns fare family, baggage allowance (CHECK_IN and CABIN),
        comboFbc, baggageFareType for this journey.
        """
        facts = _filter_facts(
            self._journey_facts,
            journey_index=self._journey_index,
            steps={BaggageChainStep.SS1_DISPLAYED, BaggageChainStep.SS1_CONSISTENCY},
        )
        header = f"SS1 displayed facts for {self._route}:"
        return f"{header}\n{_format_facts(facts)}"

    def get_sis_hold_facts(self) -> str:
        """SIS hold response — fare/FBC/baggage sent for hold.

        Returns fareId, comboFbc, baggageFareType, baggage allowance
        from the SIS hold response.
        """
        facts = _filter_facts(
            self._journey_facts,
            journey_index=self._journey_index,
            steps={BaggageChainStep.SIS_HOLD_REQUEST},
        )
        header = f"SIS-HOLD facts for {self._route}:"
        return f"{header}\n{_format_facts(facts)}"

    def get_supplier_hold_facts(self) -> str:
        """Supplier hold response — what the supplier returned after hold.

        Returns fareId, fareType, baggage details, hold status,
        and total fare from SMS_SUPPLIER_HOLD.
        """
        facts = _filter_facts(
            self._journey_facts,
            journey_index=self._journey_index,
            steps={
                BaggageChainStep.SUPPLIER_BAGGAGE_HOLD,
                BaggageChainStep.SUPPLIER_PRICE_HOLD,
            },
        )
        header = f"Supplier hold facts for {self._route}:"
        return f"{header}\n{_format_facts(facts)}"

    def get_hold_core_facts(self) -> str:
        """HOLD_CORE response — hold status and holdID.

        Returns hold success/failure, holdID, journey reference.
        """
        facts = _filter_facts(
            self._journey_facts,
            journey_index=self._journey_index,
            steps={BaggageChainStep.HOLD_CORE_RESPONSE},
        )
        header = f"HOLD_CORE facts for {self._route}:"
        return f"{header}\n{_format_facts(facts)}"

    def get_book_facts(self) -> str:
        """Booking response — what was actually booked (me-air-sms layer).

        Returns booking status, supplier PNR, ticket number.
        """
        facts = _filter_facts(
            self._journey_facts,
            journey_index=self._journey_index,
            steps={BaggageChainStep.BOOK_RESPONSE},
        )
        header = f"BOOK response facts for {self._route}:"
        return f"{header}\n{_format_facts(facts)}"

    def get_supplier_book_facts(self) -> str:
        """Supplier book response — supplier-side booking (SOAP).

        Returns PNR, booking success, baggage codes, e-ticket,
        fare basis code, and any errors from the supplier.
        """
        facts = _filter_facts(
            self._journey_facts,
            journey_index=self._journey_index,
            steps={
                BaggageChainStep.SUPPLIER_BOOK,
                BaggageChainStep.SUPPLIER_BOOK_RETRY,
            },
        )
        header = f"Supplier book facts for {self._route}:"
        return f"{header}\n{_format_facts(facts)}"

    def get_divergence_point(self) -> str:
        """Where the baggage chain invariant first broke.

        Returns the step, expected vs actual values, divergence class,
        and the citation pointing to the source file/field.
        """
        dp = self._evidence.divergence_point
        if dp is None or dp.journey_index != self._journey_index:
            return f"No divergence detected in the core baggage chain for {self._route}."

        return (
            f"Divergence detected for {self._route}:\n"
            f"  Step: {dp.step.value}\n"
            f"  Expected: {dp.expected_value}\n"
            f"  Actual: {dp.actual_value}\n"
            f"  Class: {dp.divergence_class}\n"
            f"  Source: {dp.citation.source_file} @ {dp.citation.field_path}\n"
            f"  Excerpt: {dp.citation.excerpt}"
        )

    def get_anomalies(self) -> str:
        """Anomalies and validation failures for this trip.

        Returns non-divergence issues: validation check failures,
        unexpected patterns, and anything else flagged by the evidence engine.
        """
        if not self._evidence.anomalies:
            return "No anomalies detected."

        lines = ["Anomalies:"]
        for a in self._evidence.anomalies:
            lines.append(f"  - {a}")

        if self._evidence.red_herrings:
            lines.append("\nKnown red herrings (DO NOT blame these):")
            for rh in self._evidence.red_herrings:
                lines.append(f"  - {rh}")

        return "\n".join(lines)

    def get_code_context(self) -> str:
        """Relevant source code files and functions for this incident.

        Returns code files that may be implicated, with function names
        and whether they changed after the incident date.
        """
        if not self._code_context.files:
            return "No relevant code context available."

        lines = [f"Relevant code ({len(self._code_context.files)} files):"]
        for cf in self._code_context.files[:10]:
            lines.append(f"  - {cf.repo}/{cf.path}")
            if cf.functions:
                lines.append(f"    Functions: {', '.join(cf.functions)}")

        if self._code_context.changed_after_incident:
            lines.append("\nWARNING — files changed AFTER incident date:")
            for cf in self._code_context.changed_after_incident:
                lines.append(f"  - {cf.repo}/{cf.path}")

        return "\n".join(lines)

    # --- Tool infrastructure ---

    def execute(self, tool_name: str, _arguments: dict[str, Any] | None = None) -> str:
        """Execute a tool by name. All tools are zero-argument."""
        fn = self._dispatch.get(tool_name)
        if fn is None:
            return f"Unknown tool: {tool_name}. Available: {', '.join(self._dispatch)}"
        return fn()

    def to_tool_definitions(self) -> list[dict[str, Any]]:
        """Return Groq/OpenAI function-calling tool definitions."""
        return [
            _make_tool_def(
                "get_fare_chain",
                "Get fare comparison across ALL steps for this journey: "
                "SS1 → SIS-HOLD → SUPPLIER_HOLD → BOOK. "
                "Shows fareId, comboFbc, baggageFareType at each step. "
                "Call this FIRST to identify where the fare switches.",
            ),
            _make_tool_def(
                "get_ss1_facts",
                "Get SS1 search results — what the user was SHOWN (displayed world). "
                "Returns fare family, baggage allowance (CHECK_IN and CABIN), "
                "comboFbc, baggageFareType.",
            ),
            _make_tool_def(
                "get_sis_hold_facts",
                "Get SIS hold response — fare/FBC/baggage data sent for hold. "
                "Returns fareId, comboFbc, baggageFareType, baggage allowance.",
            ),
            _make_tool_def(
                "get_supplier_hold_facts",
                "Get supplier hold response — what the supplier returned after hold. "
                "Returns fareId, fareType, baggage details, hold status, total fare.",
            ),
            _make_tool_def(
                "get_hold_core_facts",
                "Get HOLD_CORE response — hold status and holdID. "
                "Returns hold success/failure, holdID, journey reference.",
            ),
            _make_tool_def(
                "get_book_facts",
                "Get booking response — what was ACTUALLY booked (me-air-sms layer). "
                "Returns booking status, supplier PNR, ticket number.",
            ),
            _make_tool_def(
                "get_supplier_book_facts",
                "Get supplier book response — supplier-side SOAP booking. "
                "Returns PNR, success, baggage codes, e-ticket, fare basis code, errors.",
            ),
            _make_tool_def(
                "get_divergence_point",
                "Get the detected divergence point — where the baggage chain invariant "
                "FIRST broke. Returns step, expected vs actual, divergence class, citation.",
            ),
            _make_tool_def(
                "get_anomalies",
                "Get anomalies and validation failures. "
                "Returns non-divergence issues and known red herrings (which you must NOT blame).",
            ),
            _make_tool_def(
                "get_code_context",
                "Get relevant source code files and functions implicated in this incident. "
                "Includes warning if files changed after the incident date.",
            ),
        ]


def _make_tool_def(name: str, description: str) -> dict[str, Any]:
    """Build a single Groq/OpenAI function-calling tool definition.

    All investigator tools are zero-argument — the toolkit is already
    scoped to a specific journey.
    """
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }


def build_initial_context(
    evidence: EvidencePack,
    journey_index: int,
) -> str:
    """Build the minimal seed context given to the LLM before tool calling.

    Contains ONLY: trip ID, journey route, supplier, journey count.
    All evidence must be fetched via tools.
    """
    journey = next(
        (j for j in evidence.trip_index.journeys if j.journey_index == journey_index),
        None,
    )
    route = (
        f"{journey.origin} → {journey.destination}" if journey and journey.origin
        else f"journey {journey_index}"
    )
    supplier = journey.supplier if journey else evidence.supplier
    n_journeys = len(evidence.trip_index.journeys)

    return (
        f"Trip: {evidence.trip_index.trip_id}\n"
        f"Investigating journey {journey_index}: {route}\n"
        f"Supplier: {supplier}\n"
        f"Total journeys in trip: {n_journeys}\n"
        f"\nUse the tools to fetch evidence. Start with get_fare_chain."
    )
