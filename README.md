# Star Platinum

Agentic on-call RCA system that automatically diagnoses flight booking incidents from log data and code.

Given a trip ID or incident mail PDF, it fetches booking logs, traces the fare and baggage through the entire SS1→HOLD→BOOK chain per journey, detects where things diverged, and produces a root cause analysis document.

## What It Does

```
Trip ID / Mail PDF
       │
       ▼
 Fetch trip logs + payload files (126+ API calls, SOAP + JSON)
       │
       ▼
 Normalise → Call tree → Journey split → Supplier detection
       │
       ▼
 Extract facts: SS1, SIS-HOLD, SMS_SUPPLIER_HOLD, HOLD_CORE, SUPPLIER_BOOK, BOOK
       │
       ▼
 Fare chain comparison: displayed fare vs held fare vs booked fare (per journey)
       │
       ▼
 Validate: baggage, fare, FBC consistency (deterministic)
       │
       ▼
 Investigate: LLM explains WHY, per journey (Groq, structured output)
       │
       ▼
 Critic: independent verification, confidence scoring (per journey)
       │
       ▼
 RCA document with per-journey root causes (Markdown + JSON)
```

## Quick Start

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Set GROQ_API_KEY in .env

# Run (needs VPN for log API)
export $(cat .env | xargs)
python -m oncall_rca.entrypoints.rca_cli --pdf "/path/to/incident.pdf"
```

## Usage

### Full RCA pipeline (needs GROQ_API_KEY + VPN)

```bash
# From trip ID
python -m oncall_rca.entrypoints.rca_cli 5501260967375

# From Gmail PDF export
python -m oncall_rca.entrypoints.rca_cli --pdf "/path/to/mail.pdf"

# Offline (cached data only, no API calls)
python -m oncall_rca.entrypoints.rca_cli 5501260967375 --offline --cache-dir cache
```

Generates `rca_reports/<tripId>/RCA.md` with:
- Per-journey root cause with fare chain trace
- Evidence citations (log file + field path)
- Alternatives considered
- Affected code and proposed fix

### Validate a trip (no LLM, deterministic checks)

```bash
python -m oncall_rca.entrypoints.validate_cli 260802431929
python -m oncall_rca.entrypoints.validate_cli --pdf "/path/to/mail.pdf"
```

**Output:**
```
Journey 0: CAI→COK (air_arabia)
  ✅ Hold price vs Supplier price: 1253.28 AED
  ✅ Booked fare consistency: matches
  ✅ Fare basis code: P
  ✅ Baggage: CHECK_IN_BAGGAGE included, booked 30 Kg 1 Piece
  ✅ Hold status: HOLD_SUCCESS
  ✅ Booking status: Success

Summary: 6 passed, 0 failed — ALL CHECKS PASSED
```

### View normalised timeline

```bash
python -m oncall_rca.entrypoints.timeline_cli 260802431929 --offline --cache-dir cache
```

## Pipeline Stages

| # | Stage | What it does |
|---|-------|-------------|
| 1 | **Intake** | PDF or trip ID → extract trip ref, classify incident type, extract symptom |
| 2 | **Evidence** | Fetch trip index + payload files, normalise, build call tree, split journeys, extract facts from 8 baggage chain steps, find divergence, run validation checks |
| 3 | **Code Context** | Map divergent step to relevant code files via service_map.yaml (needs cloned repos) |
| 4 | **Investigator** | Per-journey LLM analysis: fare chain comparison (SS1→HOLD→BOOK), root cause hypothesis with citations |
| 5 | **Critic** | Per-journey independent verification, confidence scoring (HIGH/MEDIUM/LOW) |
| 6 | **RCA Writer** | Combine per-journey results into Markdown + JSON document |

## Baggage Chain Extractors

All 8 steps of the baggage chain are covered:

| Chain Step | API | Extractor | What it extracts |
|-----------|-----|-----------|-----------------|
| SS1_DISPLAYED | SINGLE_SOL_SEARCH | `ss1.py` | fareBenefits (CHECK_IN first), baggageFareType per fare family |
| SS1_CONSISTENCY | (cross-call) | `ss1_consistency.py` | Compare baggage across multiple SS1 calls |
| SIS_HOLD_REQUEST | HOLD | `sis_hold.py` | fareId, baggageFareType, baggage benefits from SIS-HOLD |
| SUPPLIER_BAGGAGE_HOLD | SMS_SUPPLIER_HOLD | `sms_supplier_hold.py` | passengerBaggageDetails, hold status |
| SUPPLIER_PRICE_HOLD | SMS_SUPPLIER_HOLD | `sms_supplier_hold.py` | fareId, fareType, total fare |
| HOLD_CORE_RESPONSE | HOLD_CORE | `hold_core.py` | Hold status, hold ID, journey ID |
| SUPPLIER_BOOK | SUPPLIER_BOOK | `supplier_book.py` | Booking status, baggage from SOAP response |
| BOOK_RESPONSE | BOOK | `book.py` | Booking status, fare details |

## Validation Checks

| Check | Compares | Detects |
|-------|----------|---------|
| Hold price vs Supplier price | SMS_HOLD fare ↔ SUPPLIER_PRICE_QUOTE fare | Price changed at supplier |
| Booked fare | SMS_HOLD fare ↔ SUPPLIER_BOOK fare | Fare mismatch between hold and booking |
| Fare basis code | SMS_HOLD FBC ↔ SUPPLIER_BOOK FBC | Wrong fare class booked |
| Baggage | SMS_HOLD inclusions ↔ SUPPLIER_BOOK BaggageRequest | Baggage shown but not booked |
| Hold status | SMS_HOLD response | Hold failure |
| Booking status | SUPPLIER_BOOK response | Supplier booking failure |

## Architecture

```
oncall_rca/
├── config/          Settings from .env
├── schemas/         Pydantic models (typed contracts between stages)
├── llm/             Groq client (Protocol-based, swappable)
├── tools/           Log API, file cache, code tools
├── catalogue/       service_map.yaml
├── stages/
│   ├── intake/      PDF mail parser, trip ref extraction
│   ├── preflight/   VPN reachability check
│   ├── evidence/
│   │   ├── index_normaliser.py   Raw API quirk fixes
│   │   ├── call_tree.py          Call tree, journey split, supplier detection
│   │   ├── engine.py             Orchestrates extraction + divergence
│   │   ├── divergence.py         Walks baggage chain, finds first break
│   │   ├── validator.py          Deterministic fare/baggage/FBC checks
│   │   └── extractors/           8 extractors (ss1, sis_hold, sms_supplier_hold, hold_core, supplier_book, book)
│   ├── repo_sync/   Git clone management
│   ├── code_context/ Step → relevant code files
│   ├── investigator/ Per-journey LLM hypothesis generation with fare chain comparison
│   ├── critic/       Per-journey independent verification
│   └── rca_writer/   Per-journey Markdown + JSON output
├── workflow/        End-to-end pipeline
└── entrypoints/     CLI commands (rca_cli, validate_cli, timeline_cli)
```

## Tech Stack

- **Python 3.11+**
- **Groq** (`openai/gpt-oss-120b`) for LLM reasoning
- **Pydantic** for typed schemas and structured LLM output
- **lxml** for SOAP XML parsing

## Tests

```bash
pytest                    # 162 tests
pytest -v                 # verbose
pytest tests/test_validator.py  # just validator tests
```

## Project Status

| Component | Status |
|-----------|--------|
| Log acquisition, normalisation, caching | ✅ Complete |
| Call tree, journey split (incl. multi-supplier return trips), supplier detection | ✅ Complete |
| All 8 baggage chain extractors | ✅ Complete |
| Auto-fetch payload files from log API | ✅ Complete |
| Fare/baggage/FBC validation | ✅ Complete |
| Per-journey LLM investigation with fare chain comparison | ✅ Complete |
| Per-journey critic + confidence scoring | ✅ Complete |
| Per-journey RCA document generation | ✅ Complete |
| PDF mail intake | ✅ Complete |
| SS1 consistency checking | ✅ Complete |
| Code context (repo sync + builder) | ✅ Wired, needs cloned repos |
| Gmail polling | ⏳ Needs credentials |
| Playbook engine (YAML definitions) | ⏳ Not started |
| LangGraph orchestration | ⏳ Not started |
