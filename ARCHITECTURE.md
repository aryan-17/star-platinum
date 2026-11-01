# Star Platinum Architecture

On-call RCA agent for automated incident diagnosis. Python 3.11+, LangGraph orchestration, Groq LLM with structured output.

---

## Directory Layout

```
oncall_rca/
├── schemas/            Pydantic data models
├── config/             Settings (env vars + .env)
├── llm/                LLM client (Groq + Protocol + Mock)
├── observability/      Tracing, logging, cost stats
├── workflow/           LangGraph StateGraph + pipeline runner
├── stages/             7 processing stages
│   ├── intake/         PDF email parser
│   ├── preflight/      VPN check
│   ├── evidence/       Fact extraction + divergence finding
│   │   └── extractors/ Registered payload extractors
│   ├── code_context/   Map divergent step to repo code
│   ├── investigator/   LLM hypothesis generation
│   ├── critic/         LLM independent verification
│   ├── rca_writer/     Final RCA document builder
│   └── repo_sync/      Git clone management
├── tools/              Log API, cache, code search, knowledge base
├── playbooks/          YAML-driven incident + supplier configs
├── catalogue/          service_map.yaml (api_type -> repo mapping)
├── entrypoints/        CLI commands
└── routing.py          Confidence-based auto-routing
```

---

## Pipeline Flow

```
Gmail/PDF ─> INTAKE ─> EVIDENCE ─> CODE_CONTEXT ─> INVESTIGATOR ─> CRITIC ─> ROUTING ─> RCA_WRITER ─> RCA.md
```

Each stage: `(RunState) -> RunState`. Pure function, no hidden side effects. LangGraph checkpoints after each node.

---

## 1. Intake Stage

**Purpose:** Parse incident source (PDF email) into structured `Incident`.

| File | Key | Lines |
|------|-----|-------|
| `stages/intake/pdf_mail.py` | `extract_from_pdf()` | 26-51 |
| `schemas/incident.py` | `Incident` model | 18-36 |
| `schemas/incident.py` | `IncidentType` enum | 11-15 |

**How it works:**
1. Read PDF text via PyMuPDF
2. Regex extract trip ref (12-13 digit number)
3. Keyword match against baggage terms -> classify `IncidentType`
4. Extract symptom from email body

**Graph node:** `workflow/graph.py:47-80` — creates synthetic incident if none provided, populates `RunState.incident`.

---

## 2. Evidence Stage

**Purpose:** Fetch trip data, normalize, extract facts per journey, find where the chain broke.

| File | Key | Lines |
|------|-----|-------|
| `stages/evidence/engine.py` | `build_evidence_pack()` | 32-145 |
| `stages/evidence/index_normaliser.py` | `normalise_trip_index()` | 34-100 |
| `stages/evidence/call_tree.py` | `build_call_tree()` | 31-58 |
| `stages/evidence/call_tree.py` | `split_journeys()` | 61-108 |
| `stages/evidence/call_tree.py` | `detect_supplier()` | 112-158 |
| `stages/evidence/divergence.py` | `find_first_divergence()` | 17-84 |
| `stages/evidence/validator.py` | `validate_trip()` | 77-190 |
| `schemas/evidence.py` | `EvidencePack` | 72-98 |
| `schemas/evidence.py` | `EvidenceFact` | 41-54 |
| `schemas/evidence.py` | `DivergencePoint` | 57-70 |
| `schemas/evidence.py` | `BaggageChainStep` enum | 12-25 |
| `schemas/trip_index.py` | `TripIndex` | 95-120 |
| `schemas/trip_index.py` | `Journey` | 79-93 |
| `schemas/trip_index.py` | `CallNode` | 40-77 |

**4-phase process inside `build_evidence_pack()`:**

### Phase 1: Normalize & Structure
1. `normalise_trip_index()` — raw JSON -> `TripIndex` with `CallNode` list. Fixes negative durations, deduplicates, maps pods to services, flags retries/slow/timeout anomalies.
2. `build_call_tree()` — groups calls by `identifier`, links parent/child by time order.
3. `split_journeys()` — finds SMS_BOOK calls, parses route from URL regex (`/journey/book/SUPPLIER__ORG__DST__CARRIER__`), attributes all NEW-SMS calls by host chain.
4. `detect_supplier()` — checks external calls for known supplier domains (airarabia.com, amadeus.com, etc.)

### Phase 2: Extract Facts
For each journey's calls:
- Match call API against `playbook.api_to_steps` mapping
- Read cached payload file (JSON or XML)
- Get registered `Extractor` for the step
- Call `extractor.extract()` -> list of `EvidenceFact`

Also: global SS1 extraction for unattributed supply-core calls, SS1 consistency check across multiple calls.

### Phase 3: Find Divergence
Walk playbook chain in order (`ss1_displayed` -> `ss1_consistency` -> `sis_hold_request` -> ... -> `supplier_book`). Check each step's facts for error states, failed holds, booking failures. Return first break as `DivergencePoint`.

### Phase 4: Validate
Run deterministic baggage/fare/FBC consistency checks via `validate_trip()`. Convert results to additional facts, re-check divergence.

**Graph node:** `workflow/graph.py:82-119` — fetches trip index from cache or API, calls `build_evidence_pack()`.

### Extractor Registry

| File | Step | Payload |
|------|------|---------|
| `stages/evidence/extractors/ss1.py` | `SS1_DISPLAYED` | JSON — fareBenefits + comboFbc |
| `stages/evidence/extractors/ss1_consistency.py` | `SS1_CONSISTENCY` | Logic — compare multiple SS1 calls |
| `stages/evidence/extractors/sis_hold.py` | `SIS_HOLD_REQUEST` | JSON — hold request details |
| `stages/evidence/extractors/sms_supplier_hold.py` | `SUPPLIER_BAGGAGE_HOLD`, `SUPPLIER_PRICE_HOLD` | JSON — supplier response |
| `stages/evidence/extractors/hold_core.py` | `HOLD_CORE_RESPONSE` | JSON — hold core response |
| `stages/evidence/extractors/book.py` | `BOOK_RESPONSE` | JSON — booking confirmation |
| `stages/evidence/extractors/supplier_book.py` | `SUPPLIER_BOOK` | XML (OTA) or JSON |

**Registration pattern** (`stages/evidence/extractors/base.py:26-43`):
```python
@register_extractor(BaggageChainStep.SS1_DISPLAYED)
class SS1Extractor:
    step = BaggageChainStep.SS1_DISPLAYED
    def extract(content, *, journey_index, source_file) -> list[EvidenceFact]: ...
```

`get_extractor(step)` looks up by `BaggageChainStep` enum value.

---

## 3. Code Context Stage

**Purpose:** Map divergent step to relevant source code in cloned repos.

| File | Key | Lines |
|------|-----|-------|
| `stages/code_context/builder.py` | `build_code_context()` | 30-102 |
| `stages/code_context/builder.py` | `load_service_map()` | 23-28 |
| `stages/code_context/builder.py` | `_find_service()` | 141-156 |
| `stages/repo_sync/sync.py` | `sync_repos()` | 30-101 |
| `catalogue/service_map.yaml` | API -> repo mapping | — |
| `schemas/code_context.py` | `CodeContext` | 40-55 |
| `schemas/code_context.py` | `CodeFile` | 27-38 |

**How it works:**
1. `sync_repos()` — git fetch + checkout + fast-forward for supply-core-new, air-sms, air-sms-new
2. Load `service_map.yaml` — maps `api_type` -> repo + entry point class names
3. `_find_service()` — locate target repo for divergent step's API
4. `code_grep()` — search for entry point classes in repo
5. Build `CodeFile` objects with line ranges
6. Check if files changed after incident date via `git log --since`

**Graph node:** `workflow/graph.py:121-164`

---

## 4. Investigator Stage

**Purpose:** LLM generates root-cause hypothesis per journey.

| File | Key | Lines |
|------|-----|-------|
| `stages/investigator/investigator.py` | `investigate_journey()` | 74-107 |
| `stages/investigator/investigator.py` | `_SYSTEM_PROMPT` | 16-47 |
| `schemas/hypothesis.py` | `Hypothesis` | 21-42 |

**Key design:**
- **Per-journey:** separate LLM call per journey, not one call for entire trip
- **Two-worlds principle:** supply-core (displayed to user) vs air-sms (booked with supplier)
- **Fare chain tracing:** must compare SS1 -> SIS-HOLD -> SMS_SUPPLIER_HOLD -> BOOK
- **Every claim cited:** log file + field path, or code file + line number
- **Red herring awareness:** system prompt warns against known false positives (session-expiry retry, journeyFareSummary.passengerBaggageDetails)

**LLM call:** structured output -> `Hypothesis` schema. `temperature=0.0`, `max_tokens=4096`.

**Graph node:** `workflow/graph.py:166-200` — iterates over journeys, stores `hypotheses: dict[int, Hypothesis]`.

---

## 5. Critic Stage

**Purpose:** Independent verification of each hypothesis.

| File | Key | Lines |
|------|-----|-------|
| `stages/critic/critic.py` | `critique()` | 36-68 |
| `stages/critic/critic.py` | `_SYSTEM_PROMPT` | 14-33 |
| `schemas/hypothesis.py` | `CriticReport` | 63-84 |
| `schemas/hypothesis.py` | `Confidence` enum | 13-18 |

**Key design:**
- **Fresh context:** critic does NOT see investigator's reasoning chain, only the hypothesis + evidence. Prevents error amplification.
- **3 jobs:** (1) mechanically verify each citation, (2) list alternatives, (3) assign confidence
- **Confidence rubric:**
  - HIGH: all citations valid, alternatives ruled out, timeline consistent
  - MEDIUM: most citations valid, some alternatives remain
  - LOW: citations missing/invalid, key alternatives not addressed

**Graph node:** `workflow/graph.py:202-230` — iterates over hypotheses, stores `critic_reports: dict[int, CriticReport]`.

---

## 6. Routing Stage

**Purpose:** Decide post-RCA action based on confidence.

| File | Key | Lines |
|------|-----|-------|
| `routing.py` | `route_by_confidence()` | 40-61 |
| `routing.py` | `RoutingDecision` enum | 18-23 |
| `routing.py` | `RoutingSettings` | 26-31 |

**Logic:**
- Takes **worst** confidence across all journey reports
- HIGH -> configurable (default: `human_review`)
- MEDIUM -> `human_review`
- LOW -> configurable (default: `escalate`)
- Empty reports -> `escalate`

**Graph node:** `workflow/graph.py:232-242`

---

## 7. RCA Writer Stage

**Purpose:** Combine all stage outputs into final RCA document.

| File | Key | Lines |
|------|-----|-------|
| `stages/rca_writer/writer.py` | `build_rca_doc()` | 18-112 |
| `stages/rca_writer/writer.py` | `render_rca_markdown()` | 115-170+ |
| `schemas/rca_doc.py` | `RCADoc` | 22-83 |
| `schemas/rca_doc.py` | `TimelineEntry` | 12-20 |

**Process:**
1. Normalize hypotheses/critic_reports to per-journey dicts
2. Build timeline from journey calls (sorted by time)
3. Combine per-journey root causes, citations, alternatives, affected code
4. Use worst confidence across journeys
5. Render to Markdown
6. Save to `rca_reports/<tripId>/RCA.md`

**Graph node:** `workflow/graph.py:244-280`

---

## LLM Client

| File | Key | Lines |
|------|-----|-------|
| `llm/client.py` | `LLMClient` protocol | 23-54 |
| `llm/client.py` | `GroqClient` | 69-221 |
| `llm/client.py` | `MockLLMClient` | 224-271 |
| `llm/client.py` | `LLMError`, `LLMValidationError`, `LLMBudgetExceeded` | 57-66 |

**Protocol-based:** all stages depend on `LLMClient` protocol, not concrete class. Swappable.

**GroqClient features:**
- Structured JSON output with schema validation
- Exponential backoff retries (2^attempt, max 10s)
- Token accounting + budget enforcement (`LLMBudgetExceeded`)
- Per-stage model overrides via `stage_models` dict
- Every call traced to `TraceStore`

---

## Workflow / Graph

| File | Key | Lines |
|------|-----|-------|
| `workflow/graph.py` | Graph topology (7 nodes) | 284-301 |
| `workflow/graph.py` | `build_graph()` | 284-303 |
| `workflow/pipeline.py` | `run_pipeline()` | 21-75 |

**LangGraph StateGraph** with `MemorySaver` checkpointing. Linear chain:
```
START -> intake -> evidence -> code_context -> investigator -> critic -> routing -> rca_writer -> END
```

`run_pipeline()` is the main entry point — initializes settings, LLM, cache, creates `RunState`, invokes graph.

---

## State Model

| File | Key | Lines |
|------|-----|-------|
| `schemas/run_state.py` | `RunState` | 41-90 |
| `schemas/run_state.py` | `StageRecord` | 29-38 |
| `schemas/run_state.py` | `StageStatus` enum | 19-26 |

`RunState` is the single state object threaded through all graph nodes. Contains:
- `incident`, `trip_index`, `evidence_pack`, `code_context`
- `hypotheses` (per-journey dict), `critic_reports` (per-journey dict)
- `rca_doc`, `routing_decision`
- `stages` (execution metadata), `total_tokens_used`, `total_cost_usd`
- `budget_exceeded`, `approved`, `human_notes`

**Additive-only rule:** after freeze, only new optional fields may be added.

---

## Config System

| File | Key | Lines |
|------|-----|-------|
| `config/settings.py` | `Settings` (root) | 75-90 |
| `config/settings.py` | `LogApiSettings` | 14-21 |
| `config/settings.py` | `ModelSettings` | 45-55 |
| `config/settings.py` | `BudgetSettings` | 58-63 |
| `config/settings.py` | `OutputSettings` | 66-72 |
| `config/settings.py` | `RepoSettings` | 34-42 |

Pydantic v2 `BaseSettings`. Loads from `.env` + environment variables. Nested models use env prefixes (`LOG_API_`, `GROQ_`, `OUTPUT_`, etc.).

---

## Tools

| File | Key | Lines |
|------|-----|-------|
| `tools/log_api.py` | `fetch_trip_index()` | 32-70 |
| `tools/log_api.py` | `fetch_file()` | 71-100+ |
| `tools/cache.py` | `TripCache` | 24-86 |
| `tools/code_tools.py` | `code_grep()` | 38-80 |
| `tools/code_tools.py` | `code_read()` | 83-123 |
| `tools/code_tools.py` | `git_log()` | 146-173 |
| `tools/knowledge_base.py` | `KnowledgeBase` (SQLite FTS) | 30-139 |

**Log API:** fetches trip index + payload files from internal API. Retries with backoff.

**Trip Cache:** immutable file cache (`cache/<tripId>/`). Once written, never overwritten.

**Code Tools:** ripgrep wrapper, file reader, git log. Used by code context stage.

**Knowledge Base:** SQLite + FTS5 for storing/searching past RCA results.

---

## Playbook Engine

| File | Key | Lines |
|------|-----|-------|
| `playbooks/loader.py` | `load_playbook()` | 60-101 |
| `playbooks/loader.py` | `Playbook` model | 36-58 |
| `playbooks/loader.py` | `load_supplier()` | 86-100 |
| `playbooks/loader.py` | `SupplierConfig` model | 27-34 |
| `playbooks/baggage_mismatch.yaml` | Baggage incident playbook | — |
| `playbooks/suppliers/air_arabia.yaml` | Air Arabia supplier config | — |

**Playbook** defines:
- `chain`: ordered steps for divergence walking (e.g., `ss1_displayed` -> `supplier_book`)
- `api_to_steps`: maps API name -> extraction steps
- `divergence_classes`: step -> human-readable description
- `red_herrings`: known false positives

**SupplierConfig** defines host patterns, benign errors, ticketing behavior.

Adding new incident type = new YAML file, no code changes.

---

## Observability

| File | Key | Lines |
|------|-----|-------|
| `observability/trace.py` | `TraceStore` | 30-86 |
| `observability/trace.py` | `TraceRecord` | 14-27 |
| `observability/stats.py` | `compute_stats()` | 52-89 |
| `observability/stats.py` | `format_stats()` | 92-111 |
| `observability/stats.py` | `_PRICING` (per-model costs) | 15-20 |

Every LLM call records a `TraceRecord` (stage, model, tokens, latency, success/error). `compute_stats()` aggregates into per-stage cost/latency breakdown.

---

## Entry Points

| File | Command | Lines |
|------|---------|-------|
| `entrypoints/rca_cli.py` | `python -m oncall_rca.entrypoints.rca_cli <trip_id> [--offline] [--pdf PATH]` | 16-63 |
| `entrypoints/timeline_cli.py` | `python -m oncall_rca.entrypoints.timeline_cli <trip_id> [--offline]` | 25-121 |
| `entrypoints/validate_cli.py` | `python -m oncall_rca.entrypoints.validate_cli <trip_id>` | — |
| `stages/preflight/vpn_check.py` | `check_vpn()` — test API reachability | 23-49 |

---

## Design Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| **Protocol + Registry** | `LLMClient`, `Extractor` | Swap implementations without changing stages |
| **Per-Journey Processing** | Investigator, Critic | Each journey independent; round trips are separate |
| **Playbook-Driven** | Evidence engine, divergence finder | New incident types = YAML, no code changes |
| **Two-Worlds** | Investigator prompt | supply-core (displayed) vs air-sms (booked) |
| **Fresh Critic Context** | Critic stage | Independent verification requires independence |
| **Worst-Confidence** | Routing, RCA writer | Conservative: LOW in one journey overrides HIGH in another |
| **Immutable Cache** | TripCache | Once written, never overwritten |
| **Additive-Only Schema** | RunState | No breaking changes after freeze |
