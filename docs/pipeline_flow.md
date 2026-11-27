# On-Call RCA Pipeline — Full Flow

Nov 26, 2026 · @Aryan Bahl

Complete flow of the oncall_rca LangGraph pipeline. Every stage, every branch, every tool.

## Main Pipeline Flow

```
START → INTAKE → EVIDENCE → CODE_CONTEXT → INVESTIGATOR → CRITIC → ROUTING → RCA_WRITER → END
```

> Interactive diagrams: https://claude.ai/artifact/2cos7Zt1YAtDw9KuTCpG3i

All stages run sequentially via LangGraph StateGraph. Each records COMPLETED, FAILED, or SKIPPED. MemorySaver checkpointer enables resume after crash. Data flows forward through RunState — each node returns a partial dict merged into state.

## Evidence Engine Phases

```
raw_data → Phase 1: Structure (normalise → call_tree → split_journeys → detect_supplier)
         → Phase 1b: Fetch uncached files (if online)
         → Phase 2: Extract facts (per journey + global SS1 + SS1 consistency)
         → Phase 3: Find first divergence per journey
         → Phase 4: Validate (8 checks per journey)
         → Re-check divergence with validation facts
         → EvidencePack
```

All deterministic — no LLM calls. Extractors are registered per BaggageChainStep via the factory pattern. Each extracted fact carries a citation (source file + field path). Failed validation checks become EvidenceFacts so the investigator can see them.

## Investigator ReAct Tool-Calling Loop

```
Seed (trip ID, route, supplier)
  → LLM + 10 tools (Groq function calling)
    → tool_calls? YES → toolkit.execute(name) → feed result → loop back to LLM
    → tool_calls? NO  → parse JSON → validate Hypothesis schema → return
Termination: normal answer | budget exceeded | max 10 iters | wall-clock 300s
```

The LLM receives only a seed context (trip ID, route, supplier) and 10 zero-argument tools. It decides which evidence to pull. Each tool returns pre-extracted facts with citations — no raw request/response payloads. On the final iteration, tools are dropped to force a structured JSON answer.

## Stage Details

Every stage records COMPLETED, FAILED, or SKIPPED. MemorySaver checkpoints after each stage for resume.

| Stage | Input | Output | Skip when | Fail when | Notes |
| --- | --- | --- | --- | --- | --- |
| INTAKE | incident (optional) | Incident | — | Exception creating incident | Default: BAGGAGE_MISMATCH, trip_id=manual |
| EVIDENCE | trip_id, settings, cache | EvidencePack | — | Any phase 1–4 exception | Online: fetch + cache. Offline: cache only |
| CODE_CONTEXT | evidence_pack | CodeContext | No divergence point | Repo sync fails (graceful → empty) | Greps repos for divergence step API |
| INVESTIGATOR | evidence_pack, code_context | dict[int, Hypothesis] | evidence_pack = None | Per-journey exception (skips journey) | ReAct loop, 10 tools, max 10 iters |
| CRITIC | hypotheses, evidence_pack | dict[int, CriticReport] | hypotheses empty | Per-hypothesis exception (skips it) | Fresh context, never sees investigator |
| ROUTING | critic_reports, settings | RoutingDecision | — | Exception → ESCALATE | Worst confidence across journeys wins |
| RCA_WRITER | incident, evidence, hypotheses, critics | RCADoc | Any required missing | build_rca_doc or save exception | Outputs RCA.md + rca.json |

### Routing Decision Matrix

| Worst Confidence | Action | Configurable |
| --- | --- | --- |
| HIGH | human_review | Yes (settings.high_confidence_action) |
| MEDIUM | human_review | No |
| LOW | escalate | Yes (settings.low_confidence_action) |
| (empty reports) | escalate | No |

### Investigator Tool Reference

| Tool | Returns | When to use |
| --- | --- | --- |
| get_fare_chain | fareId, comboFbc, baggageFareType at each step | First — see where fare switches |
| get_ss1_facts | Displayed world: fare family, baggage, comboFbc | What user saw |
| get_sis_hold_facts | SIS hold: fareId, comboFbc, baggage | What was held |
| get_supplier_hold_facts | Supplier response: fareId, baggage, status, total | What supplier returned |
| get_hold_core_facts | Hold status, holdID, journey ref | Did hold succeed? |
| get_book_facts | Booking status, PNR, ticket | Did booking succeed? |
| get_supplier_book_facts | Supplier SOAP: PNR, baggage codes, FBC, errors | Supplier-side booking |
| get_divergence_point | Step, expected vs actual, class, citation | Where mismatch starts |
| get_anomalies | Validation failures + red herrings | Other issues + warnings |
| get_code_context | Code files, functions, changed-after-incident | Implicated source code |
