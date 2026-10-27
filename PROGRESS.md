# Star Platinum — Implementation Progress

> Last updated: 2026-10-27

---

## Summary

Phase 1 is **60–70% complete**. Infrastructure solid (schemas, LLM client, caching, observability). Deterministic pipeline works end-to-end. Critical business logic missing (extractors, code context, playbooks) limits investigation quality. Current RCAs produce LOW confidence due to incomplete baggage chain.

---

## Done & Working

| Component | Path | Notes |
|-----------|------|-------|
| Intake (PDF + trip ID) | `stages/intake/pdf_mail.py` | Extracts trip ref, classifies incident, extracts symptom |
| Evidence Engine | `stages/evidence/engine.py` | Normalise → call tree → journey split → extract → diverge → validate |
| 3 Extractors | `stages/evidence/extractors/` | `hold_core.py`, `supplier_book.py`, `book.py` |
| Trip Index Normaliser | `stages/evidence/index_normaliser.py` | 9 quirk fixes for raw API data |
| Call Tree + Journey Split | `stages/evidence/call_tree.py` | Groups calls, detects supplier by URL |
| Divergence Finder | `stages/evidence/divergence.py` | Walks baggage chain, finds first break |
| Validator | `stages/evidence/validator.py` | Fare/baggage/FBC consistency checks |
| Investigator | `stages/investigator/investigator.py` | Groq tool-calling loop, budget-limited |
| Critic | `stages/critic/critic.py` | Independent review, confidence scoring |
| RCA Writer | `stages/rca_writer/writer.py` | Builds RCADoc, renders markdown, saves to disk |
| LLM Client | `llm/client.py` | Groq wrapper, schema validation, budget enforcement |
| Observability | `observability/` | Tracing, stats, structured logging |
| Config | `config/settings.py` + `.env` | Pydantic BaseSettings, all subsections |
| Cache | `tools/cache.py` | Trip index + file caching |
| Log API | `tools/log_api.py` | Fetch trip index, fetch files, gzip decompress |
| Timeline CLI | `entrypoints/timeline_cli.py` | Trip ID → normalised timeline JSON |
| Validate CLI | `entrypoints/validate_cli.py` | Validation demo |
| RCA CLI | `entrypoints/rca_cli.py` | Trip ID or `--pdf` → full RCA |
| Schemas | `schemas/` | All v1, frozen — Incident, EvidencePack, Hypothesis, CriticReport, RCADoc |
| Tests | `tests/` | 163 tests passing |

---

## Critical Gaps (pipeline produces weak results without these)

### 1. ~~Missing Extractors~~ — DONE (2026-10-27)

All 8 baggage chain steps now have extractors:

| Chain Step | Needed API(s) | Status |
|-----------|--------------|--------|
| `SS1_DISPLAYED` | `SINGLE_SOL_SEARCH` / `SINGLE_SOLUTION_SEARCH` | **DONE** — `extractors/ss1.py` |
| `SS1_CONSISTENCY` | Compare multiple SS1 calls | **DONE** — `extractors/ss1_consistency.py` |
| `SIS_HOLD_REQUEST` | `HOLD` (SIS-HOLD response) | **DONE** — `extractors/sis_hold.py` |
| `SUPPLIER_BAGGAGE_HOLD` | `SMS_SUPPLIER_HOLD` | **DONE** — `extractors/sms_supplier_hold.py` |
| `SUPPLIER_PRICE_HOLD` | `SMS_SUPPLIER_HOLD` | **DONE** — `extractors/sms_supplier_hold.py` |
| `HOLD_CORE_RESPONSE` | `HOLD_CORE` | Done |
| `SUPPLIER_BOOK` | `SUPPLIER_BOOK` | Done |
| `BOOK_RESPONSE` | `BOOK` | Done |

**Result:** RCA confidence improved from LOW → MEDIUM on trip 5501260967375. Root cause now specific: supply-core built SIS-HOLD request with NO_BAGGAGE fareId instead of user-selected fare.

### 2. ~~Code Context Stage Stubbed~~ — DONE (2026-10-27)

- Pipeline now calls `_code_context()` which runs `sync_repos()` + `build_code_context()`
- Degrades gracefully to empty `CodeContext()` if repos not configured/cloned
- To enable: set `REPO_SUPPLY_CORE_NEW_BRANCH`, `REPO_AIR_SMS_BRANCH` etc. in `.env` and clone repos to `~/.oncall_rca/repos/`

### 3. Playbooks & Suppliers Empty

- `playbooks/` — only `__init__.py`, no YAML definitions
- `suppliers/` — only `__init__.py`, no adapter definitions
- Red herrings hardcoded in `engine.py:37-41`
- No supplier-specific error codes or call sequences
- No playbook engine / registry

---

## Medium Gaps (works without, but fragile)

### 4. No LangGraph Integration

- Design plan §6.2 specifies LangGraph StateGraph
- Pipeline is hand-rolled sequential `_run_stage()` calls in `workflow/pipeline.py`
- Zero LangGraph imports in codebase
- No graph structure, no conditional edges

### 5. No Resumability / Checkpointing

- RunState exists in memory only
- Failed run = restart from scratch
- No serialization to disk between stages
- Design calls for checkpoint per node

### 6. VPN Preflight Not Wired

- `stages/preflight/vpn_check.py` exists and works
- Pipeline never calls it
- Fails late (at log API fetch) instead of early with clear error

### 7. Nested Settings Don't Load `.env`

- `ModelSettings` (env_prefix `GROQ_`) doesn't pick up values from `.env`
- Workaround: `export $(cat .env | xargs)` before running
- Root `Settings` loads `.env` but nested sub-models don't inherit it

---

## Not Started (Future Phases)

| Feature | Phase | Notes |
|---------|-------|-------|
| Gmail polling + SQLite dedup | Sprint 6 | No API client, no poller daemon. PDF intake works offline |
| Human review interrupt | Phase 2 | Designed in plan §7.9. Needs LangGraph |
| Fare-mismatch playbook | Phase 2 | Second incident type |
| Booking-failure playbook | Phase 2 | Third incident type |
| Multi-supplier adapters (Air Arabia, Sabre) | Phase 2 | `suppliers/` empty |
| Deploy-time correlation | Phase 3 | Pod/commit at incident time |
| Automated Claude Code handoff | Phase 4 | Agent spawns Claude Code to fix |
| Multi-model routing | Backlog | Bigger bet |
| Diff-based code context | Backlog | Bigger bet |

---

## Highest Bang-for-Buck Next Steps

1. **Build SS1 extractor** — unblocks baggage chain steps 1-2, divergence finder starts working
2. **Build SIS-HOLD extractor** — completes early chain coverage
3. **Wire code context stage** — builder + repo sync already work, just need pipeline integration
4. **Fix nested settings `.env` loading** — remove export workaround
5. **Wire VPN preflight** — fail fast with clear error

---

## Architecture (Current)

```
PDF/TripID → Intake → Evidence Engine → Code Context (stub) → Investigator → Critic → RCA Writer
                          │
                          ├── normalise_trip_index
                          ├── build_call_tree
                          ├── split_journeys
                          ├── detect_supplier
                          ├── extract facts (3 of 8 extractors)
                          ├── find_first_divergence
                          └── validate_trip
```

## Test Coverage

- 163 tests across: schemas, config, evidence engine, call tree, normaliser, code context, reasoning, LLM client, validator, PDF mail, timeline CLI, observability, stats
- Fixtures in `tests/fixtures/` and `evals/golden_incidents/`
