"""Code Context builder — maps a divergent step to relevant code files.

Uses service_map.yaml to find the repo, module, and entry points
for a given API step, then builds a CodeContext schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from oncall_rca.schemas.code_context import CodeContext, CodeFile
from oncall_rca.stages.repo_sync.sync import RepoState, check_changed_after
from oncall_rca.tools.code_tools import code_grep


_SERVICE_MAP_PATH = Path(__file__).parent.parent.parent / "catalogue" / "service_map.yaml"


def load_service_map(path: Path | None = None) -> dict[str, Any]:
    """Load service_map.yaml."""
    p = path or _SERVICE_MAP_PATH
    with open(p) as f:
        return yaml.safe_load(f)


def build_code_context(
    step_api: str,
    step_api_type: str,
    repo_states: list[RepoState],
    incident_date: str,
    *,
    service_map: dict[str, Any] | None = None,
) -> CodeContext:
    """Build CodeContext for a divergent step.

    Args:
        step_api: API name (e.g. 'SMS_HOLD', 'SUPPLIER_BOOK').
        step_api_type: API type (e.g. 'NEW-SMS', 'SUPPLY_CORE_APP_LAYER').
        repo_states: Synced repo states from repo_sync.
        incident_date: YYYY-MM-DD for changed-after check.
        service_map: Pre-loaded service map (or loads from default path).

    Returns:
        CodeContext with relevant files and commit SHAs.
    """
    if service_map is None:
        service_map = load_service_map()

    # Find matching service
    target_repo, entry_points = _find_service(step_api, step_api_type, service_map)

    if not target_repo:
        return CodeContext(
            commit_shas={r.repo: r.commit_sha for r in repo_states},
        )

    # Find repo state
    repo_state = next((r for r in repo_states if r.repo == target_repo), None)
    if not repo_state:
        return CodeContext(
            commit_shas={r.repo: r.commit_sha for r in repo_states},
        )

    # Search for entry points in the repo
    files: list[CodeFile] = []
    changed_after: list[CodeFile] = []

    for entry_point in entry_points:
        results = code_grep(repo_state.path, entry_point, max_results=5)
        for r in results:
            cf = CodeFile(
                repo=target_repo,
                path=r.file,
                functions=[entry_point],
                start_line=max(1, r.line_number - 10),
                end_line=r.line_number + 30,
            )
            files.append(cf)

            # Check if changed after incident
            if check_changed_after(repo_state.path, r.file, incident_date):
                changed_after.append(cf)

    return CodeContext(
        files=files,
        commit_shas={r.repo: r.commit_sha for r in repo_states},
        changed_after_incident=changed_after,
    )


def _find_service(
    api: str,
    api_type: str,
    service_map: dict[str, Any],
) -> tuple[str, list[str]]:
    """Find the repo and entry points for a given API step."""
    services = service_map.get("services", {})

    for service_name, config in services.items():
        repo = config.get("repo", service_name)

        # Match by api in apis list
        if api in config.get("apis", []):
            return repo, _get_entry_points(api, config)

        # Match by api_type
        if api_type in config.get("api_types", []):
            return repo, _get_entry_points(api, config)

    return "", []


def _get_entry_points(api: str, config: dict[str, Any]) -> list[str]:
    """Get entry point class/function names for an API."""
    entry_points = config.get("entry_points", {})
    if api in entry_points:
        return entry_points[api]
    # Try with common variations
    for key, values in entry_points.items():
        if key in api or api in key:
            return values
    return []
