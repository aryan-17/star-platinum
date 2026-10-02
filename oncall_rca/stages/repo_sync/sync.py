"""Repo sync — fetch and fast-forward service repos in the agent's own clones.

Never touches the user's development checkouts.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from oncall_rca.config.settings import Settings


class RepoSyncError(Exception):
    """Error during repo sync."""


@dataclass
class RepoState:
    """State of a synced repository."""

    repo: str
    path: Path
    branch: str
    commit_sha: str
    is_clean: bool


def sync_repos(settings: Settings) -> list[RepoState]:
    """Sync all configured repos to their primary branches.

    Creates clones if they don't exist, fetches and fast-forwards if they do.
    Returns the state of each repo after sync.
    """
    repos = _get_repo_configs(settings)
    results: list[RepoState] = []

    for repo_name, branch in repos:
        if not branch:
            continue  # skip unconfigured repos
        clone_path = settings.repos.clone_base / repo_name
        try:
            state = _sync_one(repo_name, clone_path, branch)
            results.append(state)
        except Exception as e:
            raise RepoSyncError(f"Failed to sync {repo_name}: {e}") from e

    return results


def get_commit_sha(repo_path: Path) -> str:
    """Get the current HEAD commit SHA of a repo."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RepoSyncError(f"git rev-parse failed: {result.stderr}")
    return result.stdout.strip()


def check_changed_after(repo_path: Path, file_path: str, since_date: str) -> bool:
    """Check if a file changed after a given date.

    Args:
        repo_path: Path to the repo clone.
        file_path: File path relative to repo root.
        since_date: ISO date string (YYYY-MM-DD).

    Returns:
        True if the file has commits after since_date.
    """
    result = subprocess.run(
        ["git", "log", "--oneline", f"--since={since_date}", "--", file_path],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _sync_one(repo_name: str, clone_path: Path, branch: str) -> RepoState:
    """Sync a single repo: fetch + fast-forward."""
    if not clone_path.exists():
        raise RepoSyncError(
            f"Clone not found at {clone_path}. "
            f"Please clone {repo_name} manually to this path."
        )

    # Fetch
    _run_git(["git", "fetch", "origin"], clone_path)

    # Checkout and fast-forward
    _run_git(["git", "checkout", branch], clone_path)
    result = subprocess.run(
        ["git", "merge", "--ff-only", f"origin/{branch}"],
        cwd=clone_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RepoSyncError(
            f"Cannot fast-forward {repo_name}/{branch}. "
            f"Clone may have diverged: {result.stderr}"
        )

    sha = get_commit_sha(clone_path)
    return RepoState(
        repo=repo_name,
        path=clone_path,
        branch=branch,
        commit_sha=sha,
        is_clean=True,
    )


def _run_git(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RepoSyncError(f"git command failed: {' '.join(cmd)}: {result.stderr}")


def _get_repo_configs(settings: Settings) -> list[tuple[str, str]]:
    """Get (repo_name, branch) pairs from settings."""
    return [
        ("supply-core-new", settings.repos.supply_core_new_branch),
        ("air-sms", settings.repos.air_sms_branch),
        ("air-sms-new", settings.repos.air_sms_new_branch),
    ]
