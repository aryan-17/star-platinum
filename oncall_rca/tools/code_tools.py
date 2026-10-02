"""Code tools — grep, read, list, git_log across service repos.

Used by the Investigator to explore code related to the divergent step.
All tools return bounded output to avoid overwhelming the LLM context.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class CodeToolError(Exception):
    """Error using a code tool."""


@dataclass
class GrepResult:
    """A single grep match."""

    file: str
    line_number: int
    content: str


@dataclass
class CodeReadResult:
    """Content of a file by line range."""

    file: str
    start_line: int
    end_line: int
    content: str
    total_lines: int


def code_grep(repo_path: Path, pattern: str, *, max_results: int = 30) -> list[GrepResult]:
    """Search code using ripgrep (falls back to grep).

    Args:
        repo_path: Path to the repo clone.
        pattern: Search pattern (regex).
        max_results: Maximum number of results to return.

    Returns:
        List of GrepResult with file, line number, and matching content.
    """
    # Try ripgrep first, fall back to grep
    result = None
    for cmd in [
        ["rg", "-n", "--max-count", str(max_results), pattern, str(repo_path)],
        ["grep", "-rn", f"--max-count={max_results}", pattern, str(repo_path)],
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode <= 1:  # 0 = found, 1 = not found
                break
        except FileNotFoundError:
            continue

    if result is None:
        raise CodeToolError("Neither rg nor grep available")

    results: list[GrepResult] = []
    for line in result.stdout.splitlines()[:max_results]:
        parts = line.split(":", 2)
        if len(parts) >= 3:
            file_path = parts[0].replace(str(repo_path) + "/", "")
            try:
                line_num = int(parts[1])
            except ValueError:
                continue
            results.append(GrepResult(
                file=file_path,
                line_number=line_num,
                content=parts[2].strip(),
            ))

    return results


def code_read(
    repo_path: Path,
    file_path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
    max_lines: int = 100,
) -> CodeReadResult:
    """Read a file by line range.

    Args:
        repo_path: Path to the repo clone.
        file_path: File path relative to repo root.
        start_line: First line to read (1-based).
        end_line: Last line to read (inclusive). None = start_line + max_lines.
        max_lines: Maximum lines to return.

    Returns:
        CodeReadResult with the file content.
    """
    full_path = repo_path / file_path
    if not full_path.exists():
        raise CodeToolError(f"File not found: {file_path}")

    lines = full_path.read_text(errors="replace").splitlines()
    total = len(lines)

    if end_line is None:
        end_line = min(start_line + max_lines - 1, total)
    end_line = min(end_line, start_line + max_lines - 1, total)

    selected = lines[start_line - 1 : end_line]
    content = "\n".join(f"{start_line + i}: {line}" for i, line in enumerate(selected))

    return CodeReadResult(
        file=file_path,
        start_line=start_line,
        end_line=end_line,
        content=content,
        total_lines=total,
    )


def code_list(repo_path: Path, glob_pattern: str = "**/*.java", *, max_results: int = 50) -> list[str]:
    """List files matching a glob pattern.

    Args:
        repo_path: Path to the repo clone.
        glob_pattern: Glob pattern to match files.
        max_results: Maximum files to return.

    Returns:
        List of file paths relative to repo root.
    """
    results: list[str] = []
    for path in sorted(repo_path.glob(glob_pattern)):
        if path.is_file() and ".git" not in path.parts:
            results.append(str(path.relative_to(repo_path)))
            if len(results) >= max_results:
                break
    return results


def git_log(
    repo_path: Path,
    file_path: str | None = None,
    *,
    since: str | None = None,
    max_entries: int = 20,
) -> str:
    """Get git log for a path, optionally since a date.

    Args:
        repo_path: Path to the repo clone.
        file_path: File path to filter (None = entire repo).
        since: ISO date string (YYYY-MM-DD) to filter from.
        max_entries: Maximum log entries.

    Returns:
        Git log output as a string.
    """
    cmd = ["git", "log", f"--max-count={max_entries}", "--oneline"]
    if since:
        cmd.append(f"--since={since}")
    if file_path:
        cmd.extend(["--", file_path])

    result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)
    if result.returncode != 0:
        raise CodeToolError(f"git log failed: {result.stderr}")
    return result.stdout.strip()
