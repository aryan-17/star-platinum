# Bigger Bets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 features: playbook engine (YAML-driven chain/divergence/red-herrings), multi-model routing (per-stage model selection), diff-based code context (git-log file discovery), confidence-based auto-routing (post-critic decision), and LangGraph StateGraph orchestration (checkpointing + resumability).

**Architecture:** Each feature is independent. Playbook engine externalises hardcoded data into YAML-loaded Pydantic models. Multi-model routing adds a `stage_models` dict to settings; the client picks the right model per `stage` param. Diff-based context supplements grep with `git log --since`. Confidence routing maps critic confidence to an action enum stored in RunState. LangGraph replaces sequential `_run_stage` calls with a compiled `StateGraph` — node functions are closures over settings/llm/cache, each returning a partial state dict.

**Tech Stack:** Python 3.11+, LangGraph 0.4+, Groq SDK, Pydantic 2.x, pydantic-settings, PyYAML, subprocess (git)

**Spec:** `oncall_rca_agent_plan.md` §7-§8, `BACKLOG.md` items 5-9

## Global Constraints

- Python 3.11+, `mypy --strict` must pass
- `ruff check .` must pass
- All 162 existing tests must continue passing
- No mutable default arguments, no bare `except:`
- All LLM outputs schema-validated via Pydantic
- Immutable state flow: stages return new state via `model_copy(update={})`
- No `Co-Authored-By` trailers in commits
- Conventional Commits format

## Review Focus

1. **Playbook YAML with missing required fields** — loader must raise `ValidationError` naming the missing field, not silently produce a partial Playbook. Test added in Task 1, Step 1.
2. **Unknown stage name in `stage_models` config** — a typo like `"investigater"` must fall back to the default model, not `KeyError`. Test added in Task 2, Step 1.
3. **Git subprocess failure in diff-based context** — repo not cloned or git not in PATH must return empty list, not crash the pipeline. Test added in Task 3, Step 1.
4. **LangGraph node exception** — a failing node must record `StageStatus.FAILED` and allow subsequent nodes to see `None` outputs, matching current `_run_stage` behaviour. Test added in Task 5, Step 1.
5. **Confidence routing with no critic reports** — if critic was skipped/failed, routing must default to `ESCALATE`, not crash. Test added in Task 4, Step 1.

---

### Task 1: Playbook Engine (YAML Definitions)

**Files:**
- Create: `oncall_rca/playbooks/__init__.py`
- Create: `oncall_rca/playbooks/loader.py` — `Playbook`, `SupplierConfig` models + `load_playbook()`, `load_supplier()`
- Create: `oncall_rca/playbooks/baggage_mismatch.yaml` — chain, api_to_steps, divergence_classes, red_herrings
- Create: `oncall_rca/playbooks/suppliers/air_arabia.yaml` — host patterns, benign errors
- Modify: `oncall_rca/stages/evidence/engine.py:32-49` — replace `_API_TO_STEPS` and `_RED_HERRINGS` with playbook parameter
- Modify: `oncall_rca/stages/evidence/divergence.py:17-27,74-82` — replace `_DIVERGENCE_CLASSES` and `chain_order` with playbook parameter
- Modify: `oncall_rca/workflow/pipeline.py:70` — pass playbook to `build_evidence_pack`
- Test: `tests/test_playbook_loader.py`

**Interfaces:**
- Consumes: `BaggageChainStep` enum from `schemas/evidence.py`
- Produces:
  - `Playbook` model with fields: `name: str`, `incident_type: str`, `chain: list[BaggageChainStep]`, `api_to_steps: dict[str, list[str]]`, `divergence_classes: dict[str, str]`, `red_herrings: list[str]`
  - `SupplierConfig` model with fields: `name: str`, `host_patterns: list[str]`, `benign_errors: list[BenignError]`, `inline_ticketing: bool`
  - `BenignError` model with fields: `code: str`, `meaning: str`
  - `load_playbook(incident_type: str) -> Playbook`
  - `load_supplier(supplier_name: str) -> SupplierConfig | None`
- Consumed by: Task 5 (LangGraph wiring passes playbook through pipeline)

- [ ] **Step 1: Write failing tests in `tests/test_playbook_loader.py`**

```python
import pytest
from oncall_rca.playbooks.loader import (
    Playbook, SupplierConfig, load_playbook, load_supplier,
)
from oncall_rca.schemas.evidence import BaggageChainStep


def test_load_baggage_mismatch_playbook():
    pb = load_playbook("baggage_mismatch")
    assert pb.name == "baggage_mismatch"
    assert pb.incident_type == "baggage_mismatch"
    assert BaggageChainStep.SS1_DISPLAYED.value in [s for s in pb.chain]
    assert len(pb.api_to_steps) > 0
    assert "SINGLE_SOLUTION_SEARCH" in pb.api_to_steps
    assert len(pb.divergence_classes) > 0
    assert len(pb.red_herrings) > 0


def test_load_unknown_playbook_raises():
    with pytest.raises(FileNotFoundError):
        load_playbook("nonexistent_playbook")


def test_load_air_arabia_supplier():
    sc = load_supplier("air_arabia")
    assert sc is not None
    assert sc.name == "Air Arabia"
    assert len(sc.host_patterns) > 0


def test_load_unknown_supplier_returns_none():
    assert load_supplier("nonexistent_airline") is None


def test_playbook_api_to_steps_values_are_valid_chain_steps():
    pb = load_playbook("baggage_mismatch")
    valid = {s.value for s in BaggageChainStep}
    for steps in pb.api_to_steps.values():
        for s in steps:
            assert s in valid, f"{s} not a valid BaggageChainStep"


def test_playbook_divergence_classes_keys_are_valid():
    pb = load_playbook("baggage_mismatch")
    valid = {s.value for s in BaggageChainStep}
    for key in pb.divergence_classes:
        assert key in valid, f"{key} not a valid BaggageChainStep"


def test_invalid_yaml_raises_validation_error(tmp_path):
    """Playbook YAML missing required fields must raise, not silently load."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("name: broken\n")  # missing all required fields
    with pytest.raises(Exception):  # ValidationError
        from oncall_rca.playbooks.loader import _load_playbook_from_path
        _load_playbook_from_path(bad_yaml)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_playbook_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'oncall_rca.playbooks'`

- [ ] **Step 3: Create `oncall_rca/playbooks/__init__.py`** (empty)

- [ ] **Step 4: Implement `Playbook`, `SupplierConfig`, `BenignError` models and loader functions in `oncall_rca/playbooks/loader.py`**

Models use Pydantic `BaseModel`. `load_playbook()` reads YAML from `Path(__file__).parent / f"{incident_type}.yaml"`, validates with `Playbook.model_validate()`. `load_supplier()` reads from `Path(__file__).parent / "suppliers" / f"{name}.yaml"`, returns `None` if file not found. Export `_load_playbook_from_path(path: Path) -> Playbook` for the invalid-YAML test.

- [ ] **Step 5: Create `oncall_rca/playbooks/baggage_mismatch.yaml`**

Port the exact values from current hardcoded constants:
- `api_to_steps` from `engine.py:32-43` (`_API_TO_STEPS`)
- `red_herrings` from `engine.py:46-49` (`_RED_HERRINGS`)
- `divergence_classes` from `divergence.py:18-27` (`_DIVERGENCE_CLASSES`)
- `chain` from `divergence.py:74-82` (`chain_order`), prepend `ss1_displayed`

- [ ] **Step 6: Create `oncall_rca/playbooks/suppliers/air_arabia.yaml`**

```yaml
name: Air Arabia
host_patterns:
  - g9routes
  - airarabia
benign_errors:
  - code: "err.2-maxico.exposed.invalid.transaction.id"
    meaning: "Session expired between hold and book — adapter retries automatically"
inline_ticketing: true
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_playbook_loader.py -v`
Expected: all PASS

- [ ] **Step 8: Wire playbook into `engine.py` — replace hardcoded `_API_TO_STEPS` and `_RED_HERRINGS`**

Add `playbook: Playbook | None = None` parameter to `build_evidence_pack()`. If `None`, call `load_playbook("baggage_mismatch")`. Build `api_to_steps` dict by converting playbook string values to `BaggageChainStep` enums. Use `playbook.red_herrings` instead of `_RED_HERRINGS`. Delete the `_API_TO_STEPS` and `_RED_HERRINGS` module-level constants.

- [ ] **Step 9: Wire playbook into `divergence.py` — replace hardcoded `_DIVERGENCE_CLASSES` and `chain_order`**

Add `playbook: Playbook | None = None` parameter to `find_first_divergence()`. If `None`, call `load_playbook("baggage_mismatch")`. Build `chain_order` from `playbook.chain` (convert strings to `BaggageChainStep`). Build `divergence_classes` dict from `playbook.divergence_classes`. Delete the `_DIVERGENCE_CLASSES` module-level constant.

- [ ] **Step 10: Run full test suite**

Run: `pytest -v`
Expected: all 162+ tests PASS (existing behaviour unchanged, playbook loads same values)

- [ ] **Step 11: Lint and type-check**

Run: `ruff check . && mypy oncall_rca/`
Expected: clean

- [ ] **Step 12: Commit**

```bash
git add oncall_rca/playbooks/ tests/test_playbook_loader.py oncall_rca/stages/evidence/engine.py oncall_rca/stages/evidence/divergence.py oncall_rca/workflow/pipeline.py
git commit -m "feat(playbook): add YAML-driven playbook engine

Replace hardcoded chain, divergence classes, red herrings, and
api-to-steps map with YAML-loaded Playbook model. Adds
baggage_mismatch.yaml and air_arabia supplier config."
```

---

### Task 2: Multi-Model Routing

**Files:**
- Modify: `oncall_rca/config/settings.py:43-49` — add `stage_models` to `ModelSettings`
- Modify: `oncall_rca/llm/client.py:69-101` — add `_stage_models` to `GroqClient.__init__` and `from_settings`; use per-stage model in `generate()`
- Test: `tests/test_llm_client.py` (add tests)

**Interfaces:**
- Consumes: `Settings.model.stage_models` dict
- Produces: `GroqClient` that selects model based on `stage` param in `generate()`
- No downstream consumers — transparent to all stage functions

- [ ] **Step 1: Write failing tests**

Add to `tests/test_llm_client.py`:

```python
def test_stage_model_override():
    """GroqClient uses stage-specific model when configured."""
    client = GroqClient(
        api_key="test",
        model_name="default-model",
        stage_models={"intake": "fast-model"},
    )
    assert client._resolve_model("intake") == "fast-model"
    assert client._resolve_model("investigator") == "default-model"


def test_unknown_stage_falls_back_to_default():
    """Typo in stage_models key must not crash — falls back to default."""
    client = GroqClient(
        api_key="test",
        model_name="default-model",
        stage_models={"investigater": "strong-model"},  # typo
    )
    assert client._resolve_model("investigator") == "default-model"


def test_stage_models_default_empty():
    """No stage_models configured — all stages use default model."""
    client = GroqClient(api_key="test", model_name="default-model")
    assert client._resolve_model("intake") == "default-model"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_client.py::test_stage_model_override -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'stage_models'`

- [ ] **Step 3: Add `stage_models` to `ModelSettings` in `settings.py`**

```python
class ModelSettings(BaseSettings):
    model_config = {"env_prefix": "GROQ_"}
    model: str = "openai/gpt-oss-120b"
    api_key: str = ""
    stage_models: dict[str, str] = Field(
        default_factory=dict,
        description="Per-stage model overrides, e.g. {'intake': 'llama-3.1-8b-instant'}",
    )
```

- [ ] **Step 4: Add `stage_models` param to `GroqClient.__init__` and add `_resolve_model` method**

Add `stage_models: dict[str, str] | None = None` to `__init__`, store as `self._stage_models`. Add method `_resolve_model(self, stage: str) -> str` that returns `self._stage_models.get(stage, self._model_name)`. In `generate()`, replace `self._model_name` with `self._resolve_model(stage)` in the API call. Update `from_settings` to pass `stage_models=settings.model.stage_models`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_llm_client.py -v`
Expected: all PASS

- [ ] **Step 6: Run full test suite + lint**

Run: `pytest -v && ruff check . && mypy oncall_rca/`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add oncall_rca/config/settings.py oncall_rca/llm/client.py tests/test_llm_client.py
git commit -m "feat(llm): add per-stage model routing

stage_models dict in ModelSettings maps stage names to model IDs.
GroqClient._resolve_model() falls back to default for unknown stages."
```

---

### Task 3: Diff-Based Code Context

**Files:**
- Modify: `oncall_rca/stages/code_context/builder.py:29-91` — add `find_recently_changed_files()`, call from `build_code_context()`
- Test: `tests/test_code_context.py` (add tests)

**Interfaces:**
- Consumes: `repo_states: list[RepoState]`, `incident_date: str`
- Produces: `find_recently_changed_files(repo_path: Path, since_date: str, max_results: int = 20) -> list[str]` — returns relative file paths
- Consumed by: `build_code_context()` internally — merged into `CodeContext.files`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_code_context.py`:

```python
from oncall_rca.stages.code_context.builder import find_recently_changed_files


def test_recently_changed_files_in_git_repo(tmp_path):
    """Finds files changed since a date in a real git repo."""
    import subprocess
    # Set up a git repo with a commit
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "file.py").write_text("x = 1")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    # Modify and commit
    (tmp_path / "file.py").write_text("x = 2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=tmp_path, capture_output=True)

    result = find_recently_changed_files(tmp_path, "2020-01-01")
    assert "file.py" in result


def test_recently_changed_files_not_a_repo(tmp_path):
    """Non-git directory returns empty list, no crash."""
    result = find_recently_changed_files(tmp_path, "2020-01-01")
    assert result == []


def test_recently_changed_files_respects_max(tmp_path):
    """max_results limits output."""
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text(f"x = {i}")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text(f"x = {i + 10}")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change all"], cwd=tmp_path, capture_output=True)

    result = find_recently_changed_files(tmp_path, "2020-01-01", max_results=2)
    assert len(result) <= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_code_context.py::test_recently_changed_files_in_git_repo -v`
Expected: FAIL — `ImportError: cannot import name 'find_recently_changed_files'`

- [ ] **Step 3: Implement `find_recently_changed_files()` in `builder.py`**

```python
def find_recently_changed_files(
    repo_path: Path,
    since_date: str,
    max_results: int = 20,
) -> list[str]:
    """Find files modified since a date using git log.

    Returns relative paths. Returns empty list on any git failure.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since_date}", "--diff-filter=M",
             "--name-only", "--pretty=format:"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []
    files = sorted(set(line.strip() for line in result.stdout.splitlines() if line.strip()))
    return files[:max_results]
```

- [ ] **Step 4: Wire into `build_code_context()` — call after grep-based search, merge results**

After the existing `for entry_point in entry_points:` loop, add:

```python
# Supplement with recently changed files
if incident_date:
    changed_files = find_recently_changed_files(repo_state.path, incident_date)
    existing_paths = {f.path for f in files}
    for rel_path in changed_files:
        if rel_path not in existing_paths and rel_path.endswith(".java"):
            cf = CodeFile(repo=target_repo, path=rel_path, functions=["(recently changed)"])
            files.append(cf)
            if check_changed_after(repo_state.path, rel_path, incident_date):
                changed_after.append(cf)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_code_context.py -v`
Expected: all PASS

- [ ] **Step 6: Run full test suite + lint**

Run: `pytest -v && ruff check . && mypy oncall_rca/`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add oncall_rca/stages/code_context/builder.py tests/test_code_context.py
git commit -m "feat(code-context): add git-log diff-based file discovery

Supplements grep-based code search with git log --since to find
recently modified files near the incident date."
```

---

### Task 4: Confidence-Based Auto-Routing

**Files:**
- Create: `oncall_rca/routing.py` — `RoutingDecision` enum, `route_by_confidence()` function
- Modify: `oncall_rca/schemas/run_state.py:40-85` — add `routing_decision` field
- Modify: `oncall_rca/config/settings.py:52-57` — add `RoutingSettings`
- Modify: `oncall_rca/workflow/pipeline.py:117-133` — call routing after critic
- Test: `tests/test_routing.py`

**Interfaces:**
- Consumes: `Confidence` enum from `schemas/hypothesis.py`, `RoutingSettings` from `config/settings.py`
- Produces:
  - `RoutingDecision` enum: `AUTO_FIX`, `HUMAN_REVIEW`, `ESCALATE`
  - `route_by_confidence(critic_reports: dict[int, CriticReport], settings: RoutingSettings) -> RoutingDecision`
  - `RunState.routing_decision: RoutingDecision | None` field
- Consumed by: Task 5 (LangGraph routing node), CLI (display routing decision)

- [ ] **Step 1: Write failing tests in `tests/test_routing.py`**

```python
import pytest
from oncall_rca.routing import RoutingDecision, route_by_confidence, RoutingSettings
from oncall_rca.schemas.hypothesis import Confidence, CriticReport


def _make_reports(confidence: Confidence) -> dict[int, CriticReport]:
    return {0: CriticReport(confidence=confidence)}


def test_high_confidence_routes_to_human_review():
    """Placeholder: HIGH maps to HUMAN_REVIEW (not AUTO_FIX until eval data exists)."""
    settings = RoutingSettings()
    result = route_by_confidence(_make_reports(Confidence.HIGH), settings)
    assert result == RoutingDecision.HUMAN_REVIEW


def test_medium_confidence_routes_to_human_review():
    settings = RoutingSettings()
    result = route_by_confidence(_make_reports(Confidence.MEDIUM), settings)
    assert result == RoutingDecision.HUMAN_REVIEW


def test_low_confidence_routes_to_escalate():
    settings = RoutingSettings()
    result = route_by_confidence(_make_reports(Confidence.LOW), settings)
    assert result == RoutingDecision.ESCALATE


def test_empty_reports_routes_to_escalate():
    """No critic reports must default to ESCALATE, not crash."""
    settings = RoutingSettings()
    result = route_by_confidence({}, settings)
    assert result == RoutingDecision.ESCALATE


def test_mixed_confidence_takes_worst():
    """Multiple journeys: worst confidence wins."""
    settings = RoutingSettings()
    reports = {
        0: CriticReport(confidence=Confidence.HIGH),
        1: CriticReport(confidence=Confidence.LOW),
    }
    result = route_by_confidence(reports, settings)
    assert result == RoutingDecision.ESCALATE


def test_custom_thresholds():
    settings = RoutingSettings(high_confidence_action="auto_fix")
    result = route_by_confidence(_make_reports(Confidence.HIGH), settings)
    assert result == RoutingDecision.AUTO_FIX
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'oncall_rca.routing'`

- [ ] **Step 3: Implement `RoutingDecision` enum, `RoutingSettings`, and `route_by_confidence()` in `oncall_rca/routing.py`**

`RoutingDecision(str, Enum)` with `AUTO_FIX`, `HUMAN_REVIEW`, `ESCALATE`. `RoutingSettings(BaseSettings)` with `high_confidence_action: str = "human_review"`, `low_confidence_action: str = "escalate"`. `route_by_confidence()` takes worst confidence across all reports (rank: LOW < MEDIUM < HIGH), maps to `RoutingDecision`. Empty reports default to `ESCALATE`.

- [ ] **Step 4: Add `routing_decision` to `RunState`**

```python
from oncall_rca.routing import RoutingDecision

class RunState(BaseModel):
    # ... existing fields ...
    routing_decision: RoutingDecision | None = None
```

- [ ] **Step 5: Add `routing` to root `Settings`**

```python
from oncall_rca.routing import RoutingSettings

class Settings(BaseSettings):
    # ... existing fields ...
    routing: Annotated[RoutingSettings, Field(default_factory=RoutingSettings)]
```

- [ ] **Step 6: Wire routing into `pipeline.py` — call after critic stage, before RCA writer**

After the critic stage block (line ~115), add:

```python
from oncall_rca.routing import route_by_confidence
routing_decision = route_by_confidence(critic_reports, settings.routing)
state = state.model_copy(update={"routing_decision": routing_decision})
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_routing.py -v`
Expected: all PASS

- [ ] **Step 8: Run full test suite + lint**

Run: `pytest -v && ruff check . && mypy oncall_rca/`
Expected: clean

- [ ] **Step 9: Commit**

```bash
git add oncall_rca/routing.py oncall_rca/schemas/run_state.py oncall_rca/config/settings.py oncall_rca/workflow/pipeline.py tests/test_routing.py
git commit -m "feat(routing): add confidence-based auto-routing

Maps critic confidence to RoutingDecision (AUTO_FIX, HUMAN_REVIEW,
ESCALATE). Placeholder thresholds — HIGH maps to HUMAN_REVIEW until
eval data calibrates the cutoff."
```

---

### Task 5: LangGraph StateGraph Wiring

**Files:**
- Create: `oncall_rca/workflow/graph.py` — `build_graph()`, node functions
- Modify: `oncall_rca/workflow/pipeline.py` — `run_pipeline()` delegates to compiled graph
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: all stage functions (`_intake`, `build_evidence_pack`, `build_code_context`, `investigate_journey`, `critique`, `build_rca_doc`), `RunState`, `Settings`, `LLMClient`, `TripCache`
- Produces:
  - `build_graph(trip_id: str, settings: Settings, llm: LLMClient, cache: TripCache, out_dir: Path, incident: Incident | None, offline: bool) -> CompiledStateGraph`
  - `run_pipeline()` signature unchanged — uses `build_graph()` internally
- No new downstream consumers — `run_pipeline()` stays the public API

- [ ] **Step 1: Write failing tests in `tests/test_graph.py`**

```python
import json
from pathlib import Path

import pytest

from oncall_rca.llm.client import MockLLMClient
from oncall_rca.schemas.hypothesis import Confidence, CriticReport, Hypothesis
from oncall_rca.schemas.run_state import RunState, StageStatus
from oncall_rca.workflow.graph import build_graph


FIXTURE_DIR = Path("evals/golden_incidents/260802431929")


@pytest.fixture
def mock_llm() -> MockLLMClient:
    client = MockLLMClient()
    client.set_response("Hypothesis", Hypothesis(
        claim="Test claim",
        explanation="Test explanation",
    ))
    client.set_response("CriticReport", CriticReport(
        all_citations_valid=True,
        confidence=Confidence.HIGH,
    ))
    return client


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    if not FIXTURE_DIR.exists():
        pytest.skip("fixture not available")
    raw = json.loads((FIXTURE_DIR / "trip_index.json").read_text())
    trip_dir = tmp_path / "cache" / "260802431929"
    trip_dir.mkdir(parents=True)
    (trip_dir / "trip_index.json").write_text(json.dumps(raw["data"]))
    return tmp_path / "cache"


def test_graph_produces_complete_state(mock_llm, cache_dir, tmp_path):
    from oncall_rca.config.settings import Settings
    from oncall_rca.tools.cache import TripCache
    graph = build_graph(
        trip_id="260802431929",
        settings=Settings(),
        llm=mock_llm,
        cache=TripCache(cache_dir),
        out_dir=tmp_path / "rca",
        incident=None,
        offline=True,
    )
    initial = RunState(run_id="test-graph")
    result = graph.invoke(initial)
    assert result["incident"] is not None
    assert result["evidence_pack"] is not None


def test_graph_records_failed_stage(mock_llm, tmp_path):
    """A node that fails must record FAILED status, not crash the graph."""
    from oncall_rca.config.settings import Settings
    from oncall_rca.tools.cache import TripCache
    # No cached data — evidence will fail
    graph = build_graph(
        trip_id="000000000000",
        settings=Settings(),
        llm=mock_llm,
        cache=TripCache(tmp_path / "empty_cache"),
        out_dir=tmp_path / "rca",
        incident=None,
        offline=True,
    )
    initial = RunState(run_id="test-fail")
    result = graph.invoke(initial)
    stages = result.get("stages", [])
    failed = [s for s in stages if s.status == StageStatus.FAILED]
    assert len(failed) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'oncall_rca.workflow.graph'`

- [ ] **Step 3: Install langgraph if not already installed**

Run: `pip install "langgraph>=0.4"`

- [ ] **Step 4: Implement `build_graph()` and node functions in `oncall_rca/workflow/graph.py`**

Use `StateGraph` with `RunState` as state schema. Each node is a closure capturing `trip_id`, `settings`, `llm`, `cache`, `out_dir`, `offline`, `incident`. Each node wraps the existing stage function in a try/except, returning a dict with the stage output field + appended `StageRecord`. Node functions: `intake_node`, `evidence_node`, `code_context_node`, `investigator_node`, `critic_node`, `routing_node`, `rca_writer_node`. Linear edge chain: START → intake → evidence → code_context → investigator → critic → routing → rca_writer → END. Compile with `MemorySaver` checkpointer.

The investigator and critic nodes handle per-journey iteration internally (same logic as current `pipeline.py:79-115`).

```python
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

def build_graph(...) -> CompiledStateGraph:
    def intake_node(state: RunState) -> dict:
        ...
    # ... other nodes ...

    graph = StateGraph(RunState)
    graph.add_node("intake", intake_node)
    # ... add all nodes and edges ...
    return graph.compile(checkpointer=MemorySaver())
```

- [ ] **Step 5: Update `run_pipeline()` in `pipeline.py` to use `build_graph()`**

Keep `run_pipeline()` signature unchanged. Internally: build graph, invoke with initial `RunState`, extract final state. The `_run_stage` and `_add_stage_record` helpers can be deleted (graph nodes handle this). Keep `_intake`, `_evidence`, `_code_context` helper functions — nodes call them.

```python
def run_pipeline(trip_id, *, settings=None, llm=None, ...) -> RunState:
    # ... setup (unchanged) ...
    graph = build_graph(trip_id, settings, llm, cache, out_dir, incident, offline)
    result = graph.invoke(
        RunState(run_id=run_id, started_at=datetime.now(tz=UTC)),
        config={"configurable": {"thread_id": run_id}},
    )
    # result is a dict or RunState depending on LangGraph version
    if isinstance(result, dict):
        return RunState.model_validate(result)
    return result
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_graph.py tests/test_pipeline.py -v`
Expected: all PASS

- [ ] **Step 7: Run full test suite + lint**

Run: `pytest -v && ruff check . && mypy oncall_rca/`
Expected: clean

- [ ] **Step 8: Commit**

```bash
git add oncall_rca/workflow/graph.py oncall_rca/workflow/pipeline.py tests/test_graph.py
git commit -m "feat(pipeline): wire LangGraph StateGraph with checkpointing

Replace sequential _run_stage calls with compiled StateGraph.
Each stage is a graph node with error handling. MemorySaver
checkpointer enables resume-after-crash. run_pipeline() signature
unchanged."
```
