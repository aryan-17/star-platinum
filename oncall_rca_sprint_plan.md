# On-Call RCA Agent — Sprint Execution Plan

**Companion to:** `oncall_rca_agent_plan.md` (design)
**Scope:** Phase 1 — baggage mismatch, Air Arabia, local runtime, human handoff to Claude Code
**Last updated:** 28 Sep 2026

---

## 1. How This Plan Works

### 1.1 The three rules

1. **Each sprint ships a standalone, working tool.** Every sprint ends with something you can run on its own from the command line and verify, without any later sprint existing.
2. **No rollback.** Later sprints only *add* to what earlier sprints built. They never require reworking, replacing, or undoing earlier work.
3. **Hard gates.** A sprint is closed only when every exit criterion is met and signed off. The next sprint does not start until then.

### 1.2 How "independent" and "no rollback" are achieved

Software built in layers always uses what came before, so independence here means each sprint **only consumes frozen contracts** from earlier sprints, never their internals. These mechanisms make that hold:

| Mechanism | What it guarantees |
|-----------|--------------------|
| **Contracts frozen in Sprint 0** | All schemas (`Incident`, `EvidencePack`, `CodeContext`, `Hypothesis`, `CriticReport`, `RCADoc`, `RunState`) are defined and versioned before any feature work. Later sprints fill them in; they don't redesign them. |
| **Additive-only change rule** | After a contract is frozen, only new optional fields may be added. A breaking change requires a new version alongside the old one, never an edit in place. |
| **Fixture-based testing** | Every sprint is tested against cached fixtures (real trip files, hand-built EvidencePacks, sample mails), so it can be verified without upstream or downstream sprints running live. |
| **Risk spikes up front** | Assumptions that could force a rewrite (Groq tool-calling quality, SOAP parsing, Gmail API access, VPN reachability from Python) are tested in Sprint 0, before anything is built on them. |
| **Permanent regression suite** | Each sprint's tests join the suite and must pass in every later sprint. Nothing that worked can silently break. |
| **Entry criteria** | Information a sprint needs (e.g. SOAP field paths) must be in hand *before* it starts, so it is never built on guesses that later need undoing. |
| **Decision log** | Every design decision is recorded with its date and reason. Reopening a decision is an explicit act, not drift. |

### 1.3 Sprint length

Sprint duration and team size are not fixed in this plan; each sprint is defined by scope and exit criteria. Set a length that suits your capacity (1–2 weeks per sprint is typical for this size of work). Sprints close on criteria, not on dates.

### 1.4 Order rationale

Sprints that need no further input from you come first (0, 1, 2). The sprint that needs the open baggage details (3) comes after, which gives time to collect them without blocking progress.

```
S0 Foundations ─► S1 Log layer ─► S2 Code intelligence ─► S3 Evidence engine ─►
S4 Reasoning ─► S5 End-to-end pipeline ─► S6 Gmail automation ─► S7 Pilot & hardening
```

S1 and S2 touch no common code and could run in parallel if you have capacity; the plan keeps them sequential to respect the gate rule.

---

## 2. Sprint Template

Every sprint below follows this structure:

- **Goal** — one sentence.
- **Entry criteria** — what must be true before it starts.
- **In scope / Out of scope.**
- **Deliverables.**
- **Standalone demo** — the command you run to see it working in isolation.
- **Exit criteria** — all must pass for sign-off.
- **Frozen at close** — what becomes a contract later sprints may rely on but not change.
- **Risks & mitigations.**

---

## Sprint 0 — Foundations, Contracts & Risk Spikes

**Goal:** Establish the project skeleton, freeze all contracts, and prove the risky assumptions before building on them.

**Entry criteria**
- Design plan approved.
- Groq API key available; VPN access from the development machine.

**In scope**
- Project structure as in design §11 (empty modules where needed).
- Configuration system: `.env` for secrets (log API token, Groq key), config file for paths, budgets, model, repo list.
- **All schemas defined and versioned (v1):** `Incident`, `TripIndex`, `CallNode`, `EvidenceFact`, `EvidencePack`, `CodeContext`, `Hypothesis`, `CriticReport`, `RCADoc`, `RunState`.
- Single LLM client wrapper for Groq: retries, timeouts, token accounting, structured (schema-validated) output.
- Observability base: run IDs, structured logging, per-call trace records (inputs, outputs, tokens, latency).
- Test harness and fixture layout (`tests/fixtures/`, `evals/golden_incidents/`).
- Fixture capture: cache the full sample trip `260802431929` (index + all files) as the first fixture.
- **Risk spikes** (throwaway code, findings documented):
  1. Groq tool-calling: can it run a 5–10 step tool loop reliably with structured output?
  2. Parse one SOAP supplier payload and one gRPC-JSON payload from the fixture.
  3. Gmail API: authenticate and list messages under a test label.
  4. Log API reachability from Python over VPN, with the static token.

**Out of scope:** any feature logic.

**Deliverables**
- Repository skeleton, config, schemas v1, LLM client, logging/tracing, test harness.
- Sample trip fixture.
- Spike report with go/no-go per assumption.

**Standalone demo**
- Run the test suite (schemas validate example objects).
- Make one schema-validated Groq call and see its trace record.

**Exit criteria**
- [ ] All schemas reviewed and signed off.
- [ ] All four spikes pass, or a documented alternative is agreed (e.g. if Flash tool-calling is weak, the design's deterministic-first approach is tightened before Sprint 4, not after).
- [ ] Sample trip fixture stored and loadable offline.
- [ ] Test suite runs green.

**Frozen at close:** schemas v1, config format, LLM client interface, trace record format, fixture layout.

**Risks & mitigations**
- *Schemas turn out incomplete later* → the additive-only rule allows new optional fields without rollback. Spend real review time here; this sprint is what makes "no rollback" possible.

---

## Sprint 1 — Log Acquisition & Normalisation

**Goal:** Turn a trip ID into a clean, normalised, journey-split timeline, fully offline-testable.

**Entry criteria:** Sprint 0 closed.

**In scope**
- VPN preflight check (reachability; distinguishes "VPN down" from "401 misconfigured").
- Trip index client (`air/book?tripId=`) and file client (`file?name=…`), with rate limiting and retries.
- Local cache: `cache/<tripId>/`, immutable once written; offline mode reads from cache only.
- Gunzip + format detection + parsing (JSON, SOAP XML, plain-text app logs).
- **Index normaliser** handling every known quirk:
  - invalid negative durations;
  - missing `req`/`res`;
  - duplicate entries;
  - files in `files_list` absent from `air_api_call`;
  - epoch timestamps in file names.
- Merge `air_api_call` with `air_book` (pod hostnames) → service mapping with **longest-prefix** pod matching.
- Call tree from `identifier`.
- Journey split (solution IDs, booking URLs, `holdId` route).
- Supplier detection by external call URL.
- Anomaly flags: retries (same external API twice), slow calls (> 10s), timeouts (> 30s).

**Out of scope:** extracting business fields (fares, baggage) — Sprint 3.

**Deliverables**
- `TripIndex` populated from live API or cache.
- CLI command producing a normalised timeline for a trip.

**Standalone demo**
- Fetch trip `260802431929` → normalised timeline and call tree, showing two journeys, Air Arabia detected, and the COK→CAI `SUPPLIER_BOOK` retry flagged.
- Same command with network off → identical output from cache.

**Exit criteria**
- [ ] Unit test for each quirk listed above.
- [ ] Snapshot test: sample trip timeline matches a reviewed "expected" output.
- [ ] Every call in the sample trip is attributed to the correct service and journey (manually verified).
- [ ] Offline mode produces byte-identical results.
- [ ] VPN-down and 401 cases produce distinct, clear errors.

**Frozen at close:** `TripIndex` / `CallNode` population behaviour, cache layout, file client interface.

**Risks & mitigations**
- *Journey attribution ambiguous for parallel calls* → host chaining + route in IDs; ambiguous facts are marked, not guessed.

---

## Sprint 2 — Code Intelligence

**Goal:** Given a service and a log step, find and return the relevant code from an up-to-date clone.

**Entry criteria:** Sprint 1 closed; primary branch name for each of the three repos provided; clone access.

**In scope**
- **Repo sync** into the agent's own clones (e.g. `~/.oncall_rca/repos/`): fetch, checkout primary branch, fast-forward only. Never touches your development checkouts.
- Record commit SHA per repo per run.
- "Changed after incident date" check for any file (git log since a date).
- `service_map.yaml` for `supply-core-new`, `air-sms`, `air-sms-new`: api_type / URL prefix / pod prefix → repo, module, and known entry points (e.g. `FlightSearchServiceGrpcImpl`, `SingleSolutionSearchWorkflow`, `HoldController`, `HoldMainWorkflow`).
- Code tools: `code_grep`, `code_read` (line ranges), `code_list`, `git_log`.
- `CodeContext` builder: step name → candidate files and functions.

**Out of scope:** any LLM reasoning over code — Sprint 4.

**Deliverables**
- Repo sync command; code tools; `CodeContext` builder.

**Standalone demo**
- Sync all three repos and print their SHAs.
- Ask for the code behind step `SIS-HOLD request` → returns `HoldController` / `HoldMainWorkflow` files in supply-core-new.

**Exit criteria**
- [ ] Sync is idempotent and fast-forward only; refuses and reports if a clone has diverged.
- [ ] Your development checkouts are never modified (verified).
- [ ] Every step in the baggage chain (design §8.3) maps to at least one code location, reviewed by you.
- [ ] Code tools return bounded, readable output (no multi-megabyte dumps).

**Frozen at close:** code tool interfaces, `CodeContext` population, `service_map.yaml` format.

**Risks & mitigations**
- *Entry points move as code changes* → map to classes/modules, not line numbers; grep fallback.

---

## Sprint 3 — Evidence Engine & Divergence Finder

**Goal:** Deterministically find where baggage diverged between the user's world (supply-core) and the booked world (air-sms), with citations.

**Entry criteria** — all open items from design §17 that the chain depends on:
- [ ] XPath/field for free baggage tier in `SUPPLIER_ANCILLARY_BAGGAGE` response.
- [ ] Where baggage appears in `SUPPLIER_BOOK` request and response.
- [ ] Whether SIS-HOLD request or HOLD_CORE response carries baggage/benefits.
- [ ] How a 2-piece allowance is represented in SS1.
- [ ] Actual baggage `benefitType` values in use.
- [ ] **At least one real baggage incident with known root cause**, cached as a fixture.

*If the purchased-ancillary path details are not yet available, that path is explicitly out of scope for this sprint and added later as a new playbook branch (additive, no rollback).*

**In scope**
- Extractors (JSON path / XPath) for: SS1 fare benefits, FBC, fare; SIS-HOLD request; HOLD_CORE response; BOOK response; `SUPPLIER_PRICE_QUOTE`, `SUPPLIER_ANCILLARY_BAGGAGE`, `SUPPLIER_BOOK`.
- `suppliers/air_arabia.yaml`: external call sequence, success indicators, error codes (incl. session-expiry retry as expected behaviour), inline ticketing.
- External call attribution to HOLD/BOOK and to the correct journey.
- `playbooks/baggage_mismatch.yaml`: chain, invariants, divergence classes, red herrings (`journeyFareSummary` baggage).
- Playbook engine + first-divergence finder.
- `EvidencePack` output with every fact citing file + field path.
- Unknown-supplier fallback (generic tracing only).

**Out of scope:** explaining *why* — Sprint 4.

**Deliverables**
- CLI: trip ID → `EvidencePack` with divergence point (or "no divergence").

**Standalone demo**
- Sample trip `260802431929` → **no divergence**; session-expiry retry recognised as expected.
- Real baggage incident fixture → divergence at the correct step, for the correct journey.

**Exit criteria**
- [ ] Negative control passes (no false divergence, retry not blamed).
- [ ] Every positive fixture finds the known divergence step.
- [ ] 100% of facts carry valid citations (automated check).
- [ ] Extractors have unit tests against real payload snippets.

**Frozen at close:** extractor interface, playbook format, supplier adapter format, `EvidencePack` population.

**Risks & mitigations**
- *Too few baggage incidents to validate* → do not close the sprint on the negative control alone; at least one positive case is an entry and exit requirement.

---

## Sprint 4 — Reasoning: Investigator & Critic

**Goal:** Explain why the divergence happened, grounded in evidence and code, with honest confidence.

**Entry criteria:** Sprints 2 and 3 closed.

**In scope**
- Investigator: LangGraph tool-calling loop on Groq, using Sprint 1–3 tools (`fetch_trip_file`, `get_evidence_fact`, code tools).
- Budgets: max iterations, tokens, wall-clock; budget exhaustion yields a marked result, never a silent failure.
- Hypothesis generation with competing alternatives; divergence-class hints from the playbook.
- Critic: fresh context, mechanical citation verification, alternatives, confidence rubric (High / Medium / Low). Annotates, never blocks.
- Versioned prompts; `skills/` with narrative know-how (two-worlds principle, Air Arabia flow, known red herrings).
- Prompt-injection wrapping for any log/mail text.

**Out of scope:** writing the RCA document; workflow wiring.

**Deliverables**
- CLI: `EvidencePack` + `CodeContext` fixture → `Hypothesis` + `CriticReport`.

**Standalone demo**
- Run on the positive baggage fixture → correct root cause with citations and confidence.
- Run on the negative control → reports no fault in the core chain, does not invent one.

**Exit criteria**
- [ ] Correct root cause on all positive fixtures (reviewed by you).
- [ ] No fabricated citations (Critic check passes 100%).
- [ ] Negative control produces no false root cause.
- [ ] Median cost and latency per run recorded and acceptable to you.

**Frozen at close:** Investigator/Critic interfaces, confidence rubric v1, prompt versions v1.

**Risks & mitigations**
- *Flash reasoning too shallow* → identified in Sprint 0 spike; mitigations are narrower questions, more deterministic hints, or thinking budget. Switching model is a config change through the LLM wrapper, not a rewrite.

---

## Sprint 5 — End-to-End Pipeline & RCA Document

**Goal:** One command takes a trip ID to a finished RCA document ready for Claude Code.

**Entry criteria:** Sprint 4 closed.

**In scope**
- LangGraph workflow wiring all stages (design §6.2), using `RunState`.
- Checkpointing after each stage; resume a failed run from the last completed stage.
- RCA Writer: `rca_reports/<tripId>/RCA.md` (template in design §13) + `evidence/` folder with cited payload excerpts.
- Commit SHAs and "changed after incident" warnings in the RCA.
- Human review step as a LangGraph interrupt.

**Out of scope:** Gmail intake — Sprint 6.

**Deliverables**
- CLI: trip ID → RCA.md + evidence folder.

**Standalone demo**
- Run on the positive fixture trip → complete RCA.
- Kill the run mid-way → resume completes without refetching or reinvestigating.
- Hand one RCA to Claude Code and see whether it can act on it.

**Exit criteria**
- [ ] RCA generated for every golden fixture, reviewed and accepted by you.
- [ ] Resume-after-failure works at every stage boundary.
- [ ] Claude Code successfully produces a sensible fix plan from at least one RCA without extra explanation.

**Frozen at close:** RCA template v1, workflow graph structure, checkpoint format.

---

## Sprint 6 — Gmail Intake & Automation

**Goal:** New incident mails under the label trigger runs automatically on your machine.

**Entry criteria:** Sprint 5 closed; Gmail label name provided; a few sample incident mails (real or representative).

**In scope**
- Gmail poller (~60s), processed-message store in SQLite.
- Trip ref extraction (12-digit regex; LLM fallback); zero or multiple matches → flagged for you.
- Incident classification (`baggage_mismatch` / `unknown`); unknown types get a clear "not supported yet" report.
- Dedupe on trip ref + thread ID.
- Queue with retry/backoff when VPN is down.
- Running as a background service on your machine (e.g. via your OS's service manager), with start/stop/status commands.

**Out of scope:** new incident types.

**Deliverables**
- Background service that turns labelled mails into RCA documents.

**Standalone demo**
- Label a test mail → RCA appears.
- Restart the service → no duplicate run.
- Disconnect VPN, label a mail, reconnect → queued incident completes.

**Exit criteria**
- [ ] All sample mails processed correctly.
- [ ] Zero duplicates across restarts and sleep/wake.
- [ ] VPN-down queueing verified.
- [ ] Mail content cannot alter agent behaviour (injection test mails pass).

**Frozen at close:** intake behaviour, state store format.

---

## Sprint 7 — Pilot, Evals & Hardening

**Goal:** Run on real incidents, measure quality, and make the system dependable for daily use.

**Entry criteria:** Sprint 6 closed.

**In scope**
- Eval harness: replay all golden fixtures offline; metrics (divergence correct, root cause correct, citation validity, confidence calibration, cost, latency).
- Regression gate: any change to prompts, playbooks, adapters, or model config must pass evals.
- Pilot: run live on real incidents for an agreed period; each RCA reviewed and outcome recorded.
- Each reviewed real incident added to the golden set.
- Operational docs: runbook, how to add a playbook, how to add a supplier adapter.

**Deliverables**
- Eval report; pilot report; runbook.

**Exit criteria**
- [ ] Agreed accuracy threshold met on the golden set and pilot incidents (you set the threshold before the pilot starts).
- [ ] No unresolved critical issues from the pilot.
- [ ] Runbook reviewed.

**Frozen at close:** Phase 1 complete.

---

## 3. After Phase 1 (each an independent, additive sprint)

Each of these adds new files or new optional fields; none changes Phase 1 contracts.

| Future sprint | Adds |
|---------------|------|
| Purchased-ancillary branch | New branch in the baggage playbook (if not done in Sprint 3) |
| Fare-mismatch playbook | New playbook using the same engine |
| Booking/ticketing-failure playbook | New playbook |
| Amadeus / Sabre adapters | New supplier adapter files |
| Knowledge base | Past RCAs searchable by the Investigator |
| Deploy-time correlation | Pod/commit at incident time; replaces the "changed after incident" warning with exact versions |
| Bitbucket access | Alternative code source alongside local clones |
| Automated Claude Code handoff | Removes the human interrupt for high-confidence categories |

---

## 4. Gate Checklist (used at every sprint close)

- [ ] All exit criteria met.
- [ ] All previous sprints' tests still pass.
- [ ] No frozen contract changed in place (only additive changes, or a new version alongside).
- [ ] Standalone demo run and accepted.
- [ ] Decision log updated.
- [ ] Next sprint's entry criteria confirmed as met, or the gap is documented and scheduled.

---

## 5. Open Inputs and Where They Are Needed

| Input | Needed by |
|-------|-----------|
| Groq API key, VPN access | Sprint 0 |
| Primary branch per repo | Sprint 2 |
| `SUPPLIER_ANCILLARY_BAGGAGE` free-tier field | Sprint 3 |
| Baggage in `SUPPLIER_BOOK` req/res | Sprint 3 |
| Baggage in SIS-HOLD / HOLD_CORE | Sprint 3 |
| 2-piece representation in SS1; `benefitType` values | Sprint 3 |
| ≥ 1 real baggage incident with known root cause | Sprint 3 |
| Purchased-ancillary path details | Sprint 3 (or a later additive sprint) |
| Gmail label name, sample mails | Sprint 6 |
| Pilot accuracy threshold | Sprint 7 |
