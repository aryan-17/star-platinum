# Star Platinum — Claude & AI Coding Standards

> Loaded every session. All rules apply to every file, every edit.

---

## Project Context

**Star Platinum** — On-call RCA agent for automated incident diagnosis.
- Python 3.11+, LangGraph orchestration, Gemini Flash LLM
- Design plan: `oncall_rca_agent_plan.md`
- Project structure: `oncall_rca/` (see plan §11)

---

## Zero-Assumption Policy

**Never assume. Always ask.**

- **No guessing APIs, field names, or payload structures.** If you haven't seen the actual response or code, ask for it. "It's probably JSON" is not good enough.
- **No guessing library APIs.** If you're unsure whether a method exists, what arguments it takes, or what it returns — look it up or ask. Don't write code against an imagined API.
- **No inferring business logic.** "Baggage is probably in the SSR segment" — stop. Ask which field, which format, which edge cases.
- **No filling in blanks with reasonable defaults.** If a config value, threshold, path, branch name, or label isn't specified, ask. Don't pick something sensible-looking.
- **No writing code you can't verify.** If you can't run it, test it, or trace it against real data — say so and ask what's needed to verify.
- **No extrapolating from one example.** One sample trip doesn't define the schema. Ask if the pattern holds or if there are variants.
- **No assuming error handling.** "It probably raises X" — check the docs or code. Ask if unsure.
- **No assuming file/payload format.** Plan says some calls are SOAP XML, some JSON. Don't guess which. Ask or read the actual file.
- **No code until requirements are locked.** If any input to your work has a ⏳ PENDING marker, flag it and ask before writing code that depends on it.

**When in doubt, your output is a question, not code.**

The cost of asking one extra question is near zero. The cost of wrong code built on wrong assumptions is a full rewrite. Bias hard toward asking.

---

## Engineering Standards

### SOLID Principles

- **Single Responsibility:** Each module, class, and function does one thing. A stage does not parse payloads AND run LLM calls. An extractor does not fetch files AND validate schemas.
- **Open/Closed:** New incident types (playbooks) and new extractors are added without modifying existing stage code. Use registries and plugin patterns, not if/elif chains.
- **Liskov Substitution:** If a base class or protocol is defined, every implementation must be substitutable without the caller knowing which concrete type it got.
- **Interface Segregation:** Typed protocols/ABCs expose only what consumers need. The Investigator doesn't see Gmail internals. The Critic doesn't see the Investigator's reasoning chain.
- **Dependency Inversion:** Stages depend on abstractions (protocols), not concrete implementations. LLM client, log API client, Gmail client — all behind interfaces so they can be swapped or mocked.

### Design Patterns to Follow

- **Strategy pattern** for playbooks — each incident type provides its own chain, extractors, and invariants without touching core Evidence logic.
- **Repository pattern** for data access — log API, Gmail, file cache, SQLite state all behind clean interfaces.
- **Pipeline pattern** for the LangGraph workflow — each stage is a pure function: `(RunState) → RunState`. No hidden side effects between stages.
- **Factory pattern** for extractors — given an `api_type` and payload format, return the right extractor. No manual dispatch.
- **Observer/hooks** for observability — stages emit events (tokens used, latency, errors). Observers log/trace without stages knowing how.

### Code Quality

- **DRY but not premature.** Extract shared logic only when the pattern appears in 3+ places. Two similar blocks are fine.
- **Explicit over implicit.** No magic strings, no string-based dispatch, no monkey-patching. Use enums, typed dicts, and Pydantic models.
- **Fail fast, fail loud.** Validation at boundaries (API responses, LLM outputs, config loading). Never silently swallow malformed data.
- **Immutable data flow.** `RunState` and stage outputs are immutable after creation. Stages return new state, never mutate in place.
- **Composition over inheritance.** Prefer protocols + composition. Inheritance only when there's a genuine "is-a" relationship with shared behavior.
- **Meaningful names.** `extract_baggage_from_supplier_book` not `process_data`. `NormalisedCall` not `Item`. Names should make the code readable without comments.
- **Small functions.** If a function needs a comment explaining what a block does, that block is a function.
- **Type everything.** All function signatures fully typed. No `Any` unless interfacing with untyped externals. `mypy --strict` should pass.

---

## Non-Negotiable Rules

### Python

- **Lint after every change:**
  ```bash
  ruff check .
  ```
- **Type-check before commit:**
  ```bash
  mypy oncall_rca/
  ```
- **Test before commit:**
  ```bash
  pytest
  ```
- **No bare `except:`.** Always catch specific exceptions.
- **No mutable default arguments.** Use `None` + assign in body.
- **No secrets in code.** All tokens/keys via `.env` or env vars.
- **Structured outputs from LLM calls** — always schema-validated.

### Git

- Conventional Commits: `feat`, `fix`, `chore`, `ci`, `docs`, `refactor`, `perf`, `test`
- Subject ≤ 50 chars, imperative mood, no period
- Scope examples: `(evidence)`, `(investigator)`, `(intake)`, `(tools)`, `(playbook)`

---

## Session Start Checklist

1. Read this file
2. Read `oncall_rca_agent_plan.md`
3. Check git log: `git log --oneline -10`
4. Confirm lint passes: `ruff check .`
