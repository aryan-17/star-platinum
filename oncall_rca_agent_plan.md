# On-Call RCA Agent — Design Plan

**Status:** Design complete except for API-dependent sections (marked **⏳ PENDING API FILE**).
**Last updated:** 28 Sep 2026

---

## 1. Goal

Build an agentic AI system in Python that, for every on-call incident mail:

1. Reads the incident mail from Gmail.
2. Extracts the trip ref and fetches all trip logs through the internal log API.
3. Analyses the logs together with the code of the services involved.
4. Produces an RCA document (Markdown) that can be handed to Claude Code to implement the fix.

## 2. Scope

**Phase 1 incident type:** *baggage mismatch* — the user was shown baggage (e.g. 2 pieces) but no baggage was assigned after booking.

**Out of scope for Phase 1:** deploy-time correlation, Bitbucket access, automated handoff to Claude Code, other incident types. These are covered in the roadmap (§16).

---

## 3. Decisions Log

| # | Topic | Decision |
|---|-------|----------|
| 1 | Mail source | Gmail, under a dedicated label |
| 2 | Trip ref | Always present in subject or body; always 12 digits; one trip ref per mail |
| 3 | Trigger | Automatic, one run per new mail |
| 4 | Logs | Two-tier internal API (index + per-file fetch); reachable only on VPN |
| 5 | Auth | Static token that never expires |
| 6 | Other signals | None (no metrics, traces, or deploy history for now) |
| 7 | Code | 3 services, all live in prod: `supply-core-new`, `air-sms`, `air-sms-new` |
| 8 | Code freshness | Pull latest primary branch of each service before every run |
| 9 | Deployment-time awareness | Deferred to a later phase |
| 10 | Baggage source | Can be fare-included allowance **or** purchased ancillary; both paths traced |
| 11 | RCA posture | Aggressive — always produce an RCA, with confidence level and alternatives |
| 12 | Output | Markdown file saved locally |
| 13 | Handoff | Human hands RCA to Claude Code for now; automated later |
| 14 | Orchestration | LangGraph |
| 15 | LLM | Single model: Gemini Flash |
| 16 | Runtime | User's local machine |
| 17 | Compliance | No constraints (redaction kept as an optional hook) |
| 18 | Payload formats | Supplier (airline) calls are SOAP XML; internal calls JSON |

---

## 4. Inputs

### 4.1 Gmail

- Polled every ~60 seconds (no public endpoint for push on a laptop).
- Processed message IDs stored locally (SQLite) to prevent duplicate runs after restarts or sleep.
- Mail content is treated as untrusted data in all prompts.

### 4.2 Log API

**Tier 1 — trip index**

```
GET https://bqapi.cleartripcorp.me/bqAPI/air/book?tripId=<TRIP_ID>&isSRE=true
Headers: token: <STATIC_TOKEN>, user: <EMAIL>
```

Response sections under `data`:

| Section | Contents | Use |
|---------|----------|-----|
| `air_api_call` | Every API call: `api_type`, `api`, `url`, `duration`, `http_code`, `host` (IP), `time`, `identifier`, `itinerary`, `req`/`res` file names, `supplier` | Primary call list |
| `air_book` | Same calls enriched with **pod hostnames** (e.g. `itinex-…`, `air-sms-new-…`), `step_name`, `event`, `trip` | Map calls → services/repos |
| `header_data` | `channel`, `itinerary[]`, `trip[]`, pax counts | Trip metadata (arrays — may hold several) |
| `air_failure_data` | Failure records | Not relevant (per user) |
| `air_wp_data` | — | Not used |
| `files_list` | Full inventory of stored files | Source of truth for what exists |

**Tier 2 — individual payload file**

```
GET https://bqapi.cleartripcorp.me/bqAPI/file?name=<FILE_NAME>&iId=<ITINERARY_ID>&date=<YYYY-MM-DD>&tripId=<TRIP_ID>
```

Returns a gzipped request or response payload.

### 4.3 Code

| Service | Pod prefixes seen in logs | Primary branch |
|---------|---------------------------|----------------|
| `supply-core-new` | `supply-core-*`, `supply-core-4hold-*` | configured per repo |
| `air-sms` | `air-sms-*` | configured per repo |
| `air-sms-new` | `air-sms-new-*`, `air-sms-new4book-*` | configured per repo |

The agent keeps **its own dedicated clones** (e.g. `~/.oncall_rca/repos/`) and never pulls into the user's development checkouts.

---

## 5. Log Data Observations (from sample trip `260802431929`)

These drive the Evidence stage design.

1. **Every call returns HTTP 200.** Failures are semantic, inside payloads. Status codes cannot be used to find problems.
2. **`files_list` contains more than `air_api_call`.** It also has app logs (`log-<ts>.gz_<itinerary>_<date>`) and flow files absent from the call list (`INITIATE_PAYMENT`, `APPLY_COUPON`, `GW_CALLBACK`, `PAYMENT_UI`, `EXTERNAL-*`, `REDIRECT-*`, `BOOK_INTERNAL-*`).
3. **`identifier` is a correlation ID** — ties a parent call to its downstream calls (e.g. `PREPAYMENT` → hold, accounts, trips, payment). Enables a call tree.
4. **Data quirks to normalise:**
   - Invalid durations such as `-1785659819433` (looks like `0 − start_epoch`) on `ANCILLARY_OFFERS`, `ACCOUNTS_UPDATE`.
   - Entries missing `req` or `res` (e.g. `UPDATE_TRIP` has no `res`).
   - Duplicate entries (same `RATE_RULE_EVALUATION` file listed twice).
   - File names encode epoch-ms timestamp and duration — usable for cross-checks.
5. **Round trips are booked as independent journeys** (sample: COK→CAI on 3L, CAI→COK on G9), each with its own `DO_BOOKING` → `SMS_BOOK` → `SUPPLIER_BOOK` chain. Baggage must be tracked **per journey and per passenger**.
6. **~300 files per trip.** Reading everything with an LLM is too slow and costly → targeted, playbook-driven fetching.

**Illustrative anomalies (not conclusions)** the Evidence stage should surface automatically:
- COK→CAI leg: first `SUPPLIER_BOOK` (1.3s) followed by re-search, price quotes and a baggage call, then a second `SUPPLIER_BOOK` (6.7s). The other leg booked in one pass.
- Two `PUT_ANCILLARY` calls on different pods 24s apart; a fresh `GET_ANCILLARY` after prepayment with no following `PUT_ANCILLARY`.

---

## 6. Architecture

### 6.1 Principles

- **Deterministic where possible, agentic where necessary.** Outer workflow is a fixed state machine; only the Investigator loops autonomously.
- **Deterministic code finds *where* things diverged; the LLM explains *why*.** Critical given a lighter model (Gemini Flash).
- **Typed contracts between every stage.**
- **Every claim cites evidence** (log file + field path, or code file + line).

### 6.2 Workflow (LangGraph)

```
 Gmail poll (label, dedupe by message ID)
        │
        ▼
 [1] Intake ─────────── classify incident type, extract 12-digit trip ref
        │                (0 or >1 matches → human review)
        ▼
 [2] VPN preflight ──── log API reachable? if not → queue & retry later
        │
        ▼
 [3] Evidence ───────── deterministic: index → timeline → call tree →
        │                playbook-driven fetch → extract facts → find divergence
        ▼
 [4] Repo sync ──────── fetch + fast-forward primary branch in agent's clones
        │
        ▼
 [5] Code Context ───── divergent step → service → repo/module → relevant code
        │
        ▼
 [6] Investigator ───── Gemini Flash tool-calling loop (step + token budget)
        │
        ▼
 [7] Critic ─────────── fresh context: verify citations, list alternatives,
        │                assign confidence (annotates, does not block)
        ▼
 [8] RCA Writer ─────── rca_reports/<tripId>/RCA.md + evidence/ folder
        │
        ▼
 [9] Human review ───── LangGraph interrupt (removed later for auto-handoff)
```

State is checkpointed after each stage so a failed run resumes from the last completed stage.

---

## 7. Stage Details

### 7.1 Intake
- **Input:** Gmail message. **Output:** `Incident`.
- Regex for 12-digit trip ref first; LLM fallback only if regex fails.
- LLM classifies incident type: `baggage_mismatch` or `unknown`.
- Extracts reported symptom text for the RCA.
- Dedupes on trip ref + Gmail thread ID (replies don't trigger new runs).

### 7.2 VPN Preflight
- Lightweight reachability check against the log API.
- On failure: incident queued with retry/backoff; no partial run.
- 401 response → configuration error, flagged immediately (token is static, so no refresh logic).

### 7.3 Evidence (no LLM in core path)
1. Fetch trip index.
2. **Normalise:** merge `air_api_call` with `air_book` (pod → service), fix invalid durations, dedupe, handle missing req/res, add extra files from `files_list`.
3. **Build call tree** from `identifier`; split per journey using solution IDs / booking URLs.
4. **Select files** according to the incident type's playbook.
5. **Fetch, gunzip, parse** (JSON or SOAP XML). Cache raw files at `cache/<tripId>/`.
6. **Run extractors** to pull specific fields (baggage allowance, SSR codes, selected ancillaries…).
7. **Find first divergence** of the invariant along the playbook chain (§8).
8. **Output:** `EvidencePack` — timeline, call tree, anomalies, facts with citations, divergence point.

### 7.4 Repo Sync
- For each of the 3 repos in the agent's own clones: fetch, checkout primary branch, fast-forward only.
- Record commit SHA per repo (written into the RCA).
- Flag if implicated files changed **after** the incident date (warning that latest code may differ from what ran).

### 7.5 Code Context
- Uses `service_map.yaml` to map `api_type` / URL prefix / pod prefix → repo + module.
- Pulls code that **builds the request** or **parses the response** at the divergent step.
- **Output:** `CodeContext`.

### 7.6 Investigator (Gemini Flash, LangGraph tool-calling loop)
- Receives: `EvidencePack`, divergence point, `CodeContext`, relevant skills.
- Forms competing hypotheses; proves/disproves each with tools.
- Can fetch additional trip files beyond the playbook if needed.
- Hard limits: max iterations, token budget, wall-clock time.
- Structured JSON output validated against `Hypothesis` schema.
- Uses thinking budget if the Flash version supports it.

### 7.7 Critic (Gemini Flash, fresh context)
- Sees evidence + hypothesis, **not** the Investigator's reasoning.
- Mechanically verifies each citation exists and says what is claimed.
- Lists alternative explanations and contradicting evidence.
- Assigns confidence (**High / Medium / Low**) from a rubric:
  citation coverage, alternatives ruled out, timeline consistency, all symptoms explained, reproduction path exists.
- Annotates only — does not block (aggressive posture).

### 7.8 RCA Writer
- Writes `rca_reports/<tripId>/RCA.md` and `rca_reports/<tripId>/evidence/` (exact payload excerpts cited).
- Template in §13.

### 7.9 Human Review
- LangGraph interrupt; human approves, then hands RCA to Claude Code.
- Later: interrupt removed for selected incident categories once eval scores justify it.

---

## 8. Baggage Mismatch Playbook

### 8.1 Technique: first divergence
Trace the baggage value **per journey and per passenger** through the flow, and find the **first step where it changes or disappears**. Deterministic code locates the step; the LLM explains the cause using surrounding payloads and code.

### 8.2 Both baggage sources
The playbook traces two paths in parallel:
- **Fare-included allowance** (from search / fare family data).
- **Purchased ancillary** (from ancillary offer → user selection).

It first determines which source applied, then follows that chain. A disagreement between the two sources is itself a finding.

### 8.3 Candidate chain ⏳ PENDING API FILE
Derived from API names in the sample; to be confirmed/corrected by the API file.

| Stage | Candidate APIs | What to check |
|-------|----------------|---------------|
| Offered | `FARE_FAMILY_INFO`, `GET_ANCILLARY`, `SMS_fetch_Ancillaries`, `SUPPLIER_ANCILLARY_BAGGAGE` | What baggage was shown |
| Selected | `PUT_ANCILLARY` (req) | What the user chose |
| Stored | `UPDATE_ITINERARY`, `VIEW_ITINERARY` | What the itinerary saved |
| Hold / price | `SMS_HOLD`, `SUPPLIER_PRICE_QUOTE`, baggage call during hold | What was held/priced |
| Book request | `SMS_BOOK`, `SUPPLIER_BOOK` (req) | What was sent to supplier |
| Book response | `SUPPLIER_BOOK` (res) | What supplier confirmed |
| Post-book | `UPDATE-TRIP-SERVICE`, `ABS_BOOKING_DETAILS` | What the trip recorded |

### 8.4 Playbook file contents
Each playbook (`playbooks/baggage_mismatch.yaml`) declares:
- Ordered API chain.
- Per step: file to open (req/res), payload format, field path (JSON path / XPath).
- Invariants that must hold across steps.
- Known quirks and red herrings.

---

## 9. Tools Catalogue

All tools are read-only, idempotent, return concise summaries with drill-down handles, and return readable errors.

| Tool | Purpose |
|------|---------|
| `gmail_poll` | List new messages under the label |
| `gmail_read` | Read one message (subject, body, thread ID) |
| `trip_index` | Fetch and normalise the trip index |
| `fetch_trip_file` | Fetch, gunzip and parse one payload (cached) |
| `get_evidence_fact` | Look up a fact from the EvidencePack by ID |
| `code_grep` | ripgrep across a repo |
| `code_read` | Read a file by line range |
| `code_list` | List / glob files |
| `git_log` | Commit history for a path, incl. since a date |
| `knowledge_base_search` | Search past incidents and RCAs |

---

## 10. Schemas (typed contracts)

| Schema | Key fields |
|--------|------------|
| `Incident` | message ID, thread ID, trip ref, incident type, reported symptom, received time |
| `TripIndex` | itineraries, trips, normalised calls, file inventory, journeys |
| `CallNode` | api, api_type, service, pod, time, duration (validated), req/res refs, children |
| `EvidenceFact` | ID, journey, passenger, step, value, source file, field path |
| `EvidencePack` | timeline, call tree, anomalies, facts, divergence point |
| `CodeContext` | repo, commit SHA, files/functions, changed-after-incident flag |
| `Hypothesis` | claim, supporting citations, contradicting evidence, affected code |
| `CriticReport` | citation checks, alternatives, confidence, rubric scores |
| `RCADoc` | all sections of the RCA template |
| `RunState` | the single object passed through LangGraph |

---

## 11. Project Structure

```
oncall_rca/
├── workflow/              # LangGraph graph, RunState, checkpointing
├── stages/
│   ├── intake/
│   ├── preflight/
│   ├── evidence/
│   │   ├── index_normaliser     # durations, dupes, missing req/res
│   │   ├── call_tree            # identifier-based tree, per-journey split
│   │   ├── extractors/          # JSON path / XPath extractors per API
│   │   └── divergence           # first-divergence finder
│   ├── repo_sync/
│   ├── code_context/
│   ├── investigator/
│   ├── critic/
│   └── rca_writer/
├── schemas/               # typed contracts (§10)
├── tools/                 # §9
├── playbooks/
│   └── baggage_mismatch.yaml    # ⏳ PENDING API FILE
├── catalogue/
│   ├── api_catalogue.yaml       # every api_type: purpose, service, format ⏳
│   └── service_map.yaml         # api_type / url / pod prefix → repo + branch
├── skills/                # narrative domain know-how, loaded on demand
├── prompts/               # versioned prompts per stage
├── guardrails/            # injection defence, output validation, budgets, redaction hook
├── llm/                   # single Gemini Flash client: retries, caching, token accounting
├── observability/         # traces, per-step logs, cost, latency
├── evals/
│   ├── golden_incidents/        # cached trips + known RCAs ⏳
│   ├── scorers
│   └── replay                   # re-run against cached files
├── cache/<tripId>/        # raw fetched files (immutable)
├── rca_reports/<tripId>/  # RCA.md + evidence/
├── state/                 # SQLite: processed messages, queue, checkpoints
├── config/                # model, thresholds, budgets, paths, token (.env)
├── entrypoints/           # poller daemon, manual CLI run, replay mode
└── tests/
```

---

## 12. Best Practices & Guardrails

- **Context engineering:** raw logs never go straight to the model; compact EvidencePack with drill-down.
- **Structured outputs:** every LLM call returns schema-validated JSON.
- **Small focused prompts per stage** rather than relying on Flash's large context window.
- **Grounding:** uncited claims are flagged by the Critic.
- **Independent critic** with fresh context.
- **Prompt-injection defence:** mail and log contents wrapped and labelled as data; system has no write access anywhere.
- **Budgets:** max Investigator iterations, tokens per stage, wall-clock per run, rate limit on log API. Hitting a limit produces an RCA marked "budget exceeded", never a silent failure.
- **Resumability:** checkpoint after each stage.
- **Observability:** trace ID per run; every LLM/tool call logged with inputs, outputs, tokens, latency.
- **Safety of local work:** agent uses its own repo clones; never touches the user's checkouts.
- **Versioning:** prompts, playbooks, skills and model config are versioned; any change must pass evals.

---

## 13. RCA Document Template

```
# RCA — Trip <tripId>

## Summary
One-paragraph root cause. Confidence: High / Medium / Low.

## Incident
Reported symptom (from mail), itinerary IDs, journeys, passengers.

## Timeline
Key steps with timestamps, service, pod, and file references.

## Divergence Point
Journey / passenger / step where baggage diverged; expected vs actual value.

## Root Cause
Explanation with citations (log file + field path; code file + line).

## Alternatives Considered
Other hypotheses and why they were ruled out or ranked lower.

## Affected Code
Repo, commit SHA analysed, files and functions.
Warning if files changed after the incident date.

## Proposed Fix
Approach (spec, not patch).

## Reproduction & Tests
Request payload(s) to reproduce; test to add.

## Acceptance Criteria
What must be true after the fix.

## Risks
Side effects, other flows touching the same code.

## Evidence
Links to files in ./evidence/
```

---

## 14. Evaluation Plan

- **Golden set:** past baggage incidents with known root causes (from the API file's worked examples) ⏳.
- **Replay mode:** re-run incidents against cached files — deterministic, no production API calls.
- **Metrics:** divergence point correct, root cause correct, citation validity, confidence calibration, cost and latency per run.
- **Gate:** any change to prompts, playbooks, skills, model or thresholds must pass the eval suite.
- **Learning loop:** human corrections and fix outcomes feed the knowledge base and become new golden cases.

---

## 15. Configuration

| Setting | Notes |
|---------|-------|
| Gmail label | Name of the incident label |
| Poll interval | ~60s |
| Log API base URL, token, user | Token in `.env` |
| Repo clone paths + primary branch per service | `supply-core-new`, `air-sms`, `air-sms-new` |
| Model | Gemini Flash; thinking budget per stage if supported |
| Budgets | Investigator iterations, tokens/stage, wall-clock/run |
| Output paths | `rca_reports/`, `cache/` |

---

## 16. Roadmap

| Phase | Scope |
|-------|-------|
| **1** | Baggage mismatch end-to-end, local runtime, human handoff to Claude Code |
| **2** | Evals hardened; more playbooks for other incident types; knowledge base of past RCAs |
| **3** | Deploy-time correlation (pod/commit at incident time); Bitbucket access |
| **4** | Automated handoff to Claude Code for high-confidence categories; optional second model |

---

## 17. Open Items

| Item | Owner | Status |
|------|-------|--------|
| API deep-dive file (purpose, fields, invariants per API) | User | ⏳ Pending |
| Worked examples of past incidents (for golden set) | User | ⏳ Pending (in API file) |
| Confirm candidate baggage chain (§8.3) | User | ⏳ Pending |
| Gmail label name | User | To provide at setup |
| Primary branch names per repo | User | To provide at setup |

---

## Appendix A — API File Template

```
## <API name as in logs, e.g. SUPPLIER_BOOK>
- api_type / pod prefix:   e.g. NEW-SMS / air-sms-new4book
- Owning repo & module:    e.g. air-sms-new, <package/class>
- Purpose:
- Stage in flow:           offer / selection / hold / book / post-book
- Check req, res, or both:
- Payload format:          JSON / SOAP XML
- Where baggage appears:   JSON path or XML element + short snippet
- What "correct" looks like:
- Known quirks / red herrings:
- Relation to other APIs:  e.g. "request should match PUT_ANCILLARY selection"

## Worked example
- Trip ID:
- Symptom as reported in the mail:
- Divergence found at (API + field):
- Actual root cause (code location if known):
- Fix that was made:
```
