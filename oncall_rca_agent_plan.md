# On-Call RCA Agent — Design Plan

**Status:** Design complete. API log reference incorporated; remaining gaps in §17.
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
| 18 | Payload formats | Supplier (airline) calls are SOAP XML; internal calls JSON; SS1 is gRPC protobuf logged as JSON |
| 19 | Debugging principle | supply-core = what the user saw; air-sms = what was booked; compare them |
| 20 | Baggage source of truth (displayed) | SS1 `fareFamilyDTO[].fareBenefits[]`; never `journeyFareSummary` baggage |
| 21 | Suppliers | Supplier-agnostic design with per-supplier adapters; Air Arabia adapter first |

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

| Service (repo) | Role | Pod prefixes in logs | Logs as | Primary branch |
|----------------|------|----------------------|---------|----------------|
| `supply-core-new` | User's world: SS1 search cache, SIS-HOLD request | `supply-core-*`, `supply-core-4hold-*` | `SUPPLY_CORE`, `HOLD`, `SMS_HOLD` (repo layer) | configured per repo |
| `air-sms` (me-air-sms, old) | Booked world: orchestrates supplier calls | `air-sms-*`, `me-air-sms4book-*` | `HOLD_CORE`, `BOOK`, `GET_SSR` | configured per repo |
| `air-sms-new` | Booked world, newer; makes **all** external supplier calls | `air-sms-new-*`, `air-sms-new4book-*` | `NEW-SMS` (`SMS_HOLD`, `SMS_BOOK`, `SMS_fetch_Ancillaries`, supplier calls) | configured per repo |

Pod-prefix matching uses **longest match first**, because `air-sms-*` would otherwise also match `air-sms-new-*`. me-air-sms often delegates to air-sms-new, so both appear in the same flow.

Other pods in the logs (`itinex-*`, `distribution-core-*`, `me-booking-handler-*`, `me-booking-terminator-*`, `me-air-vas-new-*`, `me-booking-analyser-*`) are used as log evidence only; their code is not in scope.

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

**Anomalies the Evidence stage should surface automatically** (sample trip, now explained by the API reference):
- COK→CAI leg: first `SUPPLIER_BOOK` failed with `err.2-maxico.exposed.invalid.transaction.id` (1.3s), then re-search, re-price and a second `SUPPLIER_BOOK` succeeded (6.7s, PNR 13154X). Cause: 38-minute gap between hold and book expired the Accelaero session. **Expected behaviour** — the adapter must recognise this so it isn't reported as a root cause on its own.
- Both journeys' HOLD and BOOK calls run in parallel with overlapping time windows, so journey attribution needs more than a time filter (§8.5).

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
4. **Detect supplier** from external call URLs and load the matching supplier adapter (§8.6).
5. **Attribute external calls** to HOLD/BOOK/TICKET and to the right journey using time window + host chaining + route in IDs (§8.5).
6. **Select files** according to the incident type's playbook and supplier adapter.
7. **Fetch, gunzip, parse** (JSON or SOAP XML). Cache raw files at `cache/<tripId>/`.
8. **Run extractors** to pull specific fields (fare benefits, FBC, fares, hold/book status, PNR, supplier baggage tiers…).
9. **Find first divergence** of the invariant along the playbook chain (§8).
10. **Output:** `EvidencePack` — timeline, call tree, supplier, anomalies, facts with citations, divergence point.

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

### 8.1 Core principle: two worlds

From the API reference:

> **supply-core = user's reality. air-sms (SMS) = booked reality. Compare them.**

| World | Represents | Where to read it |
|-------|-----------|------------------|
| supply-core | What the user was shown, and what we asked the supplier to book | SS1 response, SIS-HOLD request |
| air-sms / air-sms-new | What the supplier actually confirmed | HOLD_CORE response, supplier external calls, BOOK response |

If the two worlds disagree, either supply-core built the hold/book request wrongly (our bug) or the supplier returned something different (supplier-side change). The playbook's job is to find **which comparison first fails**.

### 8.2 Technique: first divergence

Trace baggage **per journey and per passenger** through the chain below and stop at the first step where the invariant breaks. Deterministic code finds the step; Gemini Flash explains the cause using the surrounding payloads and the code that built or parsed that step.

### 8.3 Baggage chain

| # | Step | File | What to extract | Invariant |
|---|------|------|-----------------|-----------|
| 1 | **SS1 — what user saw** | `SUPPLY_CORE-SINGLE_SOLUTION_SEARCH-*-res.gz` (JSON, from gRPC) | `fareFamilyDTO[].fareBenefits[]` where `benefitType` = `CHECK_IN_BAGGAGE` / cabin baggage: `qty`, `unit`, `pieceInfo`, `description`. Also `segmentFares[].fareType` and `fareBasisCode` | This is the source of truth for displayed baggage. `description = "Paid"` means not included |
| 1b | **SS1 consistency** | All SS1 calls in the trip (4+) | Same fields as step 1 | All SS1 responses for a journey must be identical |
| 2 | **SIS-HOLD request — what we asked to book** | `HOLD-SUPPLY_CORE_APP_LAYER-*-req.gz` or `SMS_HOLD-SUPPLY_CORE_REPO_LAYER-*-req.gz` | `bookedPromise.fareDetails` (fare, `journeyFareSummary[].segmentFares[].passengerTypeFareBasisDetailsList[]` FBC), `bookRequest.journeysInfo[]`, `bookRequest.supplierContext.airSupplier` | FBC and fare must match SS1. Wrong FBC → wrong fare class → wrong baggage |
| 3 | **Supplier baggage during HOLD** | `NEW-SMS` external call `SUPPLIER_ANCILLARY_BAGGAGE` res (SOAP) in the HOLD window | Free baggage tier ⏳ *field path needed* | Free tier must match SS1 `CHECK_IN_BAGGAGE` |
| 3b | **Supplier price during HOLD** | `SUPPLIER_PRICE_QUOTE` res (SOAP) | Fare and fare class ⏳ *field path needed* | Must match SIS-HOLD request |
| 4 | **HOLD_CORE response — booked reality** | `HOLD_CORE-HOLD_CORE-*-res.gz` | `holdStatus`, `fareDetails.tripFareSummary.totalAmount`, `holdId` | `HOLD_SUCCESS`; fare equals SIS-HOLD request fare |
| 5 | **BOOK** | `BOOK-BOOK-*-res.gz` (one per journey) | `bookingStatus`, `supplierPnr`, `ticketNumber` | `CNF`, PNR present; fare equals HOLD_CORE |
| 5b | **Supplier book** | `SUPPLIER_BOOK` req/res (SOAP) in the BOOK window | `<Success/>`, PNR, e-ticket; baggage in request/response ⏳ *field path needed* | Baggage sent/confirmed must match step 1 |
| 5c | **Retry check** | Repeated `SUPPLIER_BOOK`, with `findOndWiseFlightCombinations` and `SUPPLIER_PRICE_QUOTE` between them | Error of attempt 1; fare/FBC/baggage of the re-price | A retry must not change fare class or baggage |
| 6 | **Purchased ancillary path** | ⏳ *not covered in reference* | — | Selected paid baggage must reach the supplier and be confirmed |

**Do not use** `journeyFareSummary[].passengerBaggageDetails[]` for baggage (neither in SS1 nor elsewhere). The reference states it is fare-level metadata, not booked baggage. The playbook marks it as a red herring so the Investigator is told explicitly to ignore it.

### 8.4 Divergence classes and what they point to

| First failing step | Likely cause | Where the Investigator looks |
|--------------------|--------------|------------------------------|
| 1b (SS1 inconsistent) | supply-core cache returning different solutions | supply-core-new, `SingleSolutionSearchWorkflow` |
| 2 (SIS-HOLD FBC/fare ≠ SS1) | Bug constructing the hold request (our fault) | supply-core-new, `HoldController` → `HoldMainWorkflow` |
| 3 (supplier free tier ≠ SS1 benefit) | Supplier content differs from what we display, or benefit mapping is wrong | supply-core-new fare-benefit mapping; air-sms-new response parsing |
| 4 (HOLD_CORE fare ≠ SIS-HOLD) | Supplier returned a different fare | `SUPPLIER_PRICE_QUOTE` response; air-sms-new |
| 5 / 5b (booking failed or baggage missing in book) | Book request dropped baggage, or supplier rejected | air-sms book module; air-sms-new `SUPPLIER_BOOK` construction |
| 5c (retry changed fare/baggage) | Retry path re-priced into a different fare or lost baggage | air-sms-new retry flow |
| All steps match | Issue is downstream of booking (trip/post-book), or in the ancillary path | Flag as "no divergence in core chain" and report which checks passed |

### 8.5 Attributing external calls to the right journey

The reference's method is: take the main call's `time` + `duration`, then filter `api_type = "NEW-SMS"` in the same itinerary and time window, keeping calls whose `url` starts with `https://` or whose `supplier` is set.

For round trips this alone is ambiguous: both journeys' HOLDs (and BOOKs) run **in parallel over overlapping windows**. In the sample trip, both HOLD_CORE calls start at 14:44:57. The Evidence stage therefore also uses:

- **Host chaining.** Each journey's `SMS_HOLD` / `SMS_BOOK` runs on a specific `air-sms-new` pod, and its external calls come from that same host (sample: HOLD on `10.36.39.9` vs `10.36.46.10`; BOOK on `10.36.3.110` vs `10.36.75.10`).
- **Route in IDs.** `holdId` and the `/journey/book/…` URLs encode the route and flight numbers (e.g. `AIR_ARABIA__COK__CAI__3L__128…`).
- **Correlation identifiers** where they link parent and child calls.

If attribution is still ambiguous, the fact is marked as such and the Critic lowers confidence rather than guessing.

### 8.6 Supplier awareness

External call sequences differ completely by supplier. The design handles this with **supplier adapters**:

- **Supplier detection** by URL: `airarabia.com` / `accelaero.com` → Air Arabia; `amadeus.com` → Amadeus; `sabre.com` → Sabre; `flydubai.com` → FlyDubai.
- **Adapter per supplier** declares its expected HOLD/BOOK/TICKET external call sequence, success indicators, known error codes, whether ticketing is inline, and where baggage appears.
- **Phase 1: Air Arabia adapter only** (fully described in the reference).
- **Unknown supplier fallback:** generic tracing still runs (find external calls, detect retries, errors, slow calls), the Investigator reads the payloads without supplier-specific extractors, and confidence is capped at Medium.

Known Air Arabia patterns encoded in the adapter:
- `err.2-maxico.exposed.invalid.transaction.id` = stale supplier session; auto-retried. Expected when the gap between hold and book is long (worked example: 38 minutes).
- Retry sequence: `SUPPLIER_BOOK` fails → `findOndWiseFlightCombinations` → `SUPPLIER_PRICE_QUOTE` → `SUPPLIER_BOOK` again.
- Ticketing is inline with `SUPPLIER_BOOK`; no separate TICKET step.
- HTTP timeout (> 30s) → manual PNR check needed.

### 8.7 Generic red flags (all suppliers)

- Same external API called twice → first attempt failed, retry happened.
- External call > 10s → potential timeout.
- Response missing PNR or ticket → supplier error.
- Fare in response ≠ fare in request → price changed.

### 8.8 Playbook file contents
`playbooks/baggage_mismatch.yaml` declares the chain in §8.3, the invariants, the divergence classes in §8.4, and red herrings. Supplier-specific parts (external call names, SOAP XPaths, error codes) live in `suppliers/<supplier>.yaml`, so adding a supplier never requires editing the playbook.

### 8.9 Other playbooks this reference already enables
The same two-worlds comparison covers **fare mismatch** (SS1 → SIS-HOLD → HOLD_CORE → BOOK) and **booking/ticketing failure** (BOOK, SUPPLIER_BOOK, TICKET). These are cheap to add in Phase 2 because the checks are already specified.

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
│   └── baggage_mismatch.yaml    # chain, invariants, divergence classes (§8)
├── suppliers/
│   └── air_arabia.yaml          # external call sequence, XPaths, error codes, inline ticketing
├── catalogue/
│   ├── api_catalogue.yaml       # every api_type: purpose, service, format, key fields
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

- **Golden set:** past baggage incidents with known root causes ⏳ (none yet — the reference's worked example is not a baggage incident).
- **Negative control:** trip `260802431929` is a healthy booking (fares match, both PNRs CNF, retry expected). The system must report "no divergence" and must **not** blame the session-expiry retry.
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
| **2** | Evals hardened; fare-mismatch and booking-failure playbooks; Amadeus/Sabre adapters; knowledge base of past RCAs |
| **3** | Deploy-time correlation (pod/commit at incident time); Bitbucket access |
| **4** | Automated handoff to Claude Code for high-confidence categories; optional second model |

---

## 17. Open Items

| Item | Owner | Status |
|------|-------|--------|
| API log reference | User | ✅ Received and incorporated |
| Where supplier baggage appears in `SUPPLIER_ANCILLARY_BAGGAGE` response (free tier field / XPath) | User | ⏳ Needed |
| Where baggage appears in `SUPPLIER_BOOK` request and response (SSR / XPath) | User | ⏳ Needed |
| Whether SIS-HOLD request or HOLD_CORE response carries baggage/benefits at all | User | ⏳ Needed |
| How a 2-piece allowance is represented in SS1 (`pieceInfo: 2`? `unit: PIECE`?) | User | ⏳ Needed |
| Baggage benefitType values (`CABIN_BAGGAGE` vs `HAND_BAGGAGE` both appear in the reference) | User | ⏳ Needed |
| Purchased-ancillary path: which APIs/fields (GET_ANCILLARY, PUT_ANCILLARY, SMS_fetch_Ancillaries…) | User | ⏳ Needed |
| At least 1–3 real baggage incidents with known root cause (golden set) | User | ⏳ Needed |
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
