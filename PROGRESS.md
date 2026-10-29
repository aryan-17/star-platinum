# Star Platinum — Implementation Progress

> Last updated: 2026-10-29

---

## Summary

Phase 1 is **85–90% complete**. Full baggage chain extraction (all 8 steps), per-journey investigation, fare chain comparison, auto file fetching, and multi-supplier return trip support all working. RCA quality at MEDIUM confidence on real incidents. Remaining: playbook engine, LangGraph wiring, Gmail polling.

---

## Done & Working

| Component | Path | Notes |
|-----------|------|-------|
| Intake (PDF + trip ID) | `stages/intake/pdf_mail.py` | Extracts trip ref, classifies incident, extracts symptom |
| Evidence Engine | `stages/evidence/engine.py` | Normalise → call tree → journey split → extract → diverge → validate |
| All 8 Extractors | `stages/evidence/extractors/` | SS1, SS1 consistency, SIS-HOLD, SMS_SUPPLIER_HOLD (baggage+price), HOLD_CORE, SUPPLIER_BOOK, BOOK |
| Auto file fetch | `stages/evidence/engine.py` | Fetches uncached payload files from log API before extraction |
| SS1 global extraction | `stages/evidence/engine.py` | SS1 calls extracted for all journeys (pre-journey search phase) |
| Trip Index Normaliser | `stages/evidence/index_normaliser.py` | 9 quirk fixes for raw API data |
| Call Tree + Journey Split | `stages/evidence/call_tree.py` | Groups calls, detects supplier, splits return trips by SMS_SUPPLIER_HOLD hosts |
| Divergence Finder | `stages/evidence/divergence.py` | Walks baggage chain, finds first break |
| Validator | `stages/evidence/validator.py` | Fare/baggage/FBC consistency checks |
| Per-journey Investigator | `stages/investigator/investigator.py` | Separate LLM analysis per journey with fare chain comparison |
| Per-journey Critic | `stages/critic/critic.py` | Independent review + confidence per journey |
| Per-journey RCA Writer | `stages/rca_writer/writer.py` | Combines per-journey root causes into single report |
| LLM Client | `llm/client.py` | Groq wrapper, schema validation, budget enforcement |
| Resilient schema parsing | `schemas/code_context.py` | Coerces non-numeric LLM line numbers |
| Observability | `observability/` | Tracing, stats, structured logging |
| Config | `config/settings.py` + `.env` | Pydantic BaseSettings, all subsections |
| Cache | `tools/cache.py` | Trip index + file caching |
| Log API | `tools/log_api.py` | Fetch trip index, fetch files, gzip decompress |
| Timeline CLI | `entrypoints/timeline_cli.py` | Trip ID → normalised timeline JSON |
| Validate CLI | `entrypoints/validate_cli.py` | Validation demo |
| RCA CLI | `entrypoints/rca_cli.py` | Trip ID or `--pdf` → full RCA |
| Schemas | `schemas/` | All v1, frozen — Incident, EvidencePack, Hypothesis, CriticReport, RCADoc, RunState (with per-journey fields) |
| Tests | `tests/` | 162 tests passing |

---

## Completed Since Initial Audit (2026-10-27 → 2026-10-29)

| PR | Change | Impact |
|----|--------|--------|
| #20 | 5 new extractors (SS1, SIS-HOLD, SMS_SUPPLIER_HOLD baggage+price) + wire into engine + code context | RCA LOW → MEDIUM |
| #21 | Prioritize fareBenefits over baggageFareType in SS1 | CHECK_IN baggage seen first |
| #22 | Per-journey evidence in investigator prompt | LLM investigates all legs |
| #23 | Detect return journey from SMS_SUPPLIER_HOLD hosts + fix supplier detection | Multi-supplier return trips split correctly |
| #24 | Coerce CodeFile line numbers from LLM output | No more "ninety" validation crashes |
| #25 | Auto-fetch payload files from log API | Trips without pre-cached files now work |
| #26 | Fare chain comparison in investigator prompt | LLM starts with displayed vs booked fare |
| #27 | Extract SS1 facts globally across all journeys | SS1 search-phase data visible for return leg |
| #28 | Per-journey investigation + critic + RCA sections | Each journey gets its own root cause |
| #29 | README update | Documents current pipeline state |

---

## Remaining Gaps

### Still Important

| Gap | Priority | Notes |
|-----|----------|-------|
| Playbook engine (YAML definitions) | Medium | Red herrings hardcoded, no supplier-specific error codes |
| VPN preflight not wired | Low | Fails late instead of early |
| Nested settings `.env` loading | Low | Workaround: `export $(cat .env | xargs)` |

### Future Phases

| Feature | Phase | Notes |
|---------|-------|-------|
| LangGraph orchestration | Phase 2 | Hand-rolled pipeline works, no resumability |
| Gmail polling + SQLite dedup | Sprint 6 | PDF intake works, no auto-trigger |
| Fare-mismatch playbook | Phase 2 | Second incident type |
| Booking-failure playbook | Phase 2 | Third incident type |
| Multi-supplier adapters | Phase 2 | Currently supplier detection only |
| Deploy-time correlation | Phase 3 | Pod/commit at incident time |
| Automated Claude Code handoff | Phase 4 | Agent spawns Claude Code to fix |
| Multi-model routing | Backlog | Cheap model for intake, strong for investigation |
| Diff-based code context | Backlog | git log --since for recently changed files |

---

## Architecture (Current)

```
PDF/TripID → Intake → Evidence Engine → Code Context → Investigator (×N) → Critic (×N) → RCA Writer
                          │                                    │
                          ├── normalise_trip_index              ├── per-journey prompt
                          ├── build_call_tree                   ├── fare chain comparison
                          ├── split_journeys (incl. return)     ├── focused evidence
                          ├── detect_supplier                   └── structured hypothesis
                          ├── fetch missing files
                          ├── extract facts (8 extractors, incl. global SS1)
                          ├── SS1 consistency check
                          ├── find_first_divergence
                          └── validate_trip
```
