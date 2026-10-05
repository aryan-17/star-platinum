# Feature Backlog

Features to build. Ordered by priority within each category.

---

## High Impact, Low Effort

### 1. Slack webhook for RCA delivery
Post RCA summary to a Slack channel when generated. One `httpx.post()` with webhook URL from `.env`. Solves notification without Gmail OAuth.

### 2. Integration test with real Groq
Run full pipeline against golden fixture with actual Groq API call (not mock). Validates structured output + schema compliance end-to-end. Guard with `@pytest.mark.integration` and `GROQ_API_KEY` env check.

### 3. `--live` flag on rca_cli
Fetch from API directly, cache, and run pipeline in one command. Plumbing exists — just needs CLI wiring. Currently requires pre-cached data or `--offline`.

---

## Bigger Bets

### 4. LangGraph proper wiring
Replace sequential function calls in `pipeline.py` with actual LangGraph `StateGraph`. Gets checkpointing, resume-after-crash, and human-in-the-loop interrupt for free. Main refactor is converting `_run_stage` into LangGraph nodes.

### 5. Multi-model routing
Cheap model (`llama-3.1-8b`) for intake/classification, strong model (`gpt-oss-120b`) for investigation. Config per stage. Cut cost ~60% on easy stages.

### 6. Diff-based code context
Instead of grepping current code, use `git log --since=<incident_date> --diff-filter=M` to find recently changed files. Often the bug is in a recent commit. Supplements the current grep-based approach.

### 7. Confidence-based auto-routing
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

| Item | Needed for |
|------|-----------|
| SS1 `fareFamilyDTO` field path for baggage | SS1 extractor |
| SIS-HOLD request baggage field path | SIS-HOLD extractor |
| ≥1 real baggage incident with known root cause | Positive-case validation |
| Gmail credentials + label | Sprint 6 |
| Repo clone paths + branch names | Code context stage |
| Pilot accuracy threshold | Sprint 7 eval gate |
