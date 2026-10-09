# Star Platinum

Agentic on-call RCA system that automatically diagnoses flight booking incidents from log data and code.

Given a trip ID or incident mail, it fetches booking logs, traces values through the entire hold→book→supplier chain, detects where things diverged, and produces a root cause analysis document.

## What It Does

```
Trip ID / Mail PDF
       │
       ▼
 Fetch trip logs (126+ API calls, SOAP + JSON payloads)
       │
       ▼
 Normalise → Call tree → Journey split → Supplier detection
       │
       ▼
 Validate: baggage, fare, FBC consistency (deterministic)
       │
       ▼
 Investigate: LLM explains WHY (Groq, structured output)
       │
       ▼
 Critic: independent verification, confidence scoring
       │
       ▼
 RCA document (Markdown + JSON)
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
```

## Usage

### Validate a trip (no LLM, deterministic checks)

```bash
# By trip ID (fetches from API, needs VPN)
python -m oncall_rca.entrypoints.validate_cli 260802431929

# From cached data
python -m oncall_rca.entrypoints.validate_cli 260802431929 --offline --cache-dir cache

# From a Gmail PDF export
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

### Full RCA pipeline (needs GROQ_API_KEY)

```bash
python -m oncall_rca.entrypoints.rca_cli 260802431929
python -m oncall_rca.entrypoints.rca_cli --pdf "/path/to/mail.pdf"
```

Generates `rca_reports/<tripId>/RCA.md` with root cause, evidence citations, and fix proposal.

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
├── schemas/         10 Pydantic models (typed contracts between stages)
├── llm/             Groq client (Protocol-based, swappable)
├── tools/           Log API, file cache, code tools, knowledge base
├── stages/
│   ├── intake/      PDF mail parser, trip ref extraction
│   ├── preflight/   VPN reachability check
│   ├── evidence/    Normaliser, call tree, extractors, validator, divergence finder
│   ├── repo_sync/   Git clone management
│   ├── code_context/ Step → relevant code files
│   ├── investigator/ LLM hypothesis generation
│   ├── critic/       Independent verification
│   └── rca_writer/   Markdown + JSON output
├── workflow/        End-to-end pipeline
└── entrypoints/     CLI commands
```

## Tech Stack

- **Python 3.11+**
- **Groq** (`openai/gpt-oss-120b`) for LLM reasoning
- **Pydantic** for typed schemas and structured LLM output
- **lxml** for SOAP XML parsing
- **SQLite + FTS5** for knowledge base

## Tests

```bash
pytest                    # 163 tests
pytest -v                 # verbose
pytest tests/test_validator.py  # just validator tests
```

## Project Status

| Component | Status |
|-----------|--------|
| Log acquisition & normalisation | ✅ Complete |
| Call tree, journey split, supplier detection | ✅ Complete |
| Fare/baggage/FBC validation | ✅ Complete |
| Evidence extractors (HOLD_CORE, SUPPLIER_BOOK, BOOK) | ✅ Complete |
| LLM investigation + critic | ✅ Complete |
| RCA document generation | ✅ Complete |
| PDF mail intake | ✅ Complete |
| Knowledge base | ✅ Complete |
| Playbook generator | ✅ Complete |
| Gmail polling | ⏳ Needs credentials |
| Code context (repo sync) | ⏳ Needs clone paths |
