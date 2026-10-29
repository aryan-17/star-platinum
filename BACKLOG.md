# Feature Backlog

Features to build. Ordered by priority within each category.

> Last updated: 2026-10-29

---

## High Impact, Low Effort

### 1. Slack webhook for RCA delivery
Post RCA summary to a Slack channel when generated. One `httpx.post()` with webhook URL from `.env`. Solves notification without Gmail OAuth.

### 2. Integration test with real Groq
Run full pipeline against golden fixture with actual Groq API call (not mock). Validates structured output + schema compliance end-to-end. Guard with `@pytest.mark.integration` and `GROQ_API_KEY` env check.

### 3. Wire VPN preflight check
`stages/preflight/vpn_check.py` exists and works. Just needs to be called in pipeline before evidence stage. Fail fast with clear error instead of failing at log API fetch.

### 4. Fix nested settings `.env` loading
`ModelSettings` (env_prefix `GROQ_`) doesn't load from `.env`. Workaround: `export $(cat .env | xargs)`. Fix: use `model_config` with `env_file` on nested models or consolidate into root Settings.

---

## Bigger Bets

### 5. LangGraph proper wiring
Replace sequential function calls in `pipeline.py` with actual LangGraph `StateGraph`. Gets checkpointing, resume-after-crash, and human-in-the-loop interrupt for free. Main refactor is converting `_run_stage` into LangGraph nodes.

### 6. Playbook engine (YAML definitions)
Red herrings are hardcoded in `engine.py`. Supplier-specific error codes and call sequences not defined. Need `playbooks/baggage_mismatch.yaml` and `suppliers/air_arabia.yaml` with divergence-class hints, expected retry patterns, known benign errors.

### 7. Multi-model routing
Cheap model (`llama-3.1-8b`) for intake/classification, strong model (`gpt-oss-120b`) for investigation. Config per stage. Cut cost ~60% on easy stages.

### 8. Diff-based code context
Instead of grepping current code, use `git log --since=<incident_date> --diff-filter=M` to find recently changed files. Often the bug is in a recent commit. Supplements the current grep-based approach.

### 9. Confidence-based auto-routing
High confidence → auto-generate fix PR. Medium → human review. Low → escalate to on-call team. Requires Sprint 7 eval data to calibrate thresholds.

---

## Sprint 6: Gmail Intake (Deferred)

Blocked on Gmail credentials + label name. When ready:
- Gmail poller (~60s)
- Processed message store (SQLite)
- Trip ref extraction (12-digit regex, LLM fallback)
- Incident classification
- Dedupe on trip ref + thread ID
- Queue with retry/backoff when VPN down

---

## Sprint 7: Pilot & Hardening (Deferred)

Blocked on Sprint 6 + real incidents. When ready:
- Eval harness: replay golden fixtures, compute metrics
- Regression gate: changes to prompts/playbooks must pass evals
- Pilot: live run on real incidents, review each RCA
- Operational docs: runbook, how to add playbook/supplier adapter

---

## Data Gaps (Need User Input)

| Item | Needed for | Status |
|------|-----------|--------|
| ~~SS1 `fareFamilyDTO` field path for baggage~~ | ~~SS1 extractor~~ | ✅ Resolved — `fareBenefits[].baggageAllowance` |
| ~~SIS-HOLD request baggage field path~~ | ~~SIS-HOLD extractor~~ | ✅ Resolved — `flightSolution.fareFamilies[].journeyFareInfo` |
| ~~≥1 real baggage incident with known root cause~~ | ~~Positive-case validation~~ | ✅ Tested with 3 real incidents |
| Gmail credentials + label | Sprint 6 | ⏳ |
| Repo clone paths + branch names | Code context stage | ⏳ |
| Pilot accuracy threshold | Sprint 7 eval gate | ⏳ |
