"""Log API clients — trip index and individual file fetch.

Based on the working bq_logs.py patterns:
- CORS headers required (origin, referer)
- Token/user fields accept any value
- Files are gzipped, need decompression
- API accepts both tripId and iId parameters
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from oncall_rca.config.settings import Settings
from oncall_rca.stages.preflight.vpn_check import _build_headers


class LogAPIError(Exception):
    """Error fetching data from the log API."""


class TripNotFound(LogAPIError):
    """Trip ID returned no data."""


def fetch_trip_index(
    trip_id: str,
    settings: Settings,
    *,
    timeout: int = 60,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Fetch the full trip index from the log API.

    Args:
        trip_id: 12-digit trip reference.
        settings: Application settings.
        timeout: Request timeout in seconds.
        max_retries: Number of retry attempts.

    Returns:
        Parsed JSON response data (the 'data' key from the API response).

    Raises:
        TripNotFound: If the API returns status != 1.
        LogAPIError: On network or parsing failure.
    """
    base = settings.log_api.base_url
    url = f"{base}/air/book?tripId={urllib.parse.quote(trip_id)}&isSRE=true"
    headers = _build_headers(settings)

    raw = _http_get_with_retry(url, headers, timeout=timeout, max_retries=max_retries)

    try:
        resp = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LogAPIError(f"Failed to parse trip index JSON: {e}") from e

    if resp.get("status") != 1:
        raise TripNotFound(f"Trip {trip_id} not found or API error: {resp}")

    return resp["data"]


def fetch_file(
    file_name: str,
    itinerary_id: str,
    date: str,
    trip_id: str,
    settings: Settings,
    *,
    timeout: int = 30,
    max_retries: int = 3,
) -> bytes:
    """Fetch and decompress a single payload file from the log API.

    Args:
        file_name: File name as it appears in the trip index.
        itinerary_id: Itinerary ID (e.g. NIX...).
        date: Date string YYYY-MM-DD.
        trip_id: Trip reference.
        settings: Application settings.

    Returns:
        Decompressed file content as bytes.
    """
    base = settings.log_api.base_url
    params = urllib.parse.urlencode({
        "name": file_name,
        "iId": itinerary_id,
        "date": date,
        "tripId": trip_id,
    })
    url = f"{base}/file?{params}"
    headers = _build_headers(settings)

    raw = _http_get_with_retry(url, headers, timeout=timeout, max_retries=max_retries)

    # Attempt gzip decompression; fall back to raw bytes
    try:
        return gzip.decompress(raw)
    except (gzip.BadGzipFile, OSError):
        return raw


def detect_format(content: bytes) -> str:
    """Detect payload format from content.

    Returns:
        'json', 'soap_xml', or 'plain_text'.
    """
    stripped = content.lstrip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        return "json"
    if stripped.startswith(b"<"):
        return "soap_xml"
    return "plain_text"


def parse_payload(content: bytes) -> Any:
    """Parse payload content based on detected format.

    Returns:
        Parsed JSON (dict/list), lxml Element (XML), or decoded string (plain text).
    """
    fmt = detect_format(content)
    if fmt == "json":
        return json.loads(content)
    if fmt == "soap_xml":
        from lxml import etree
        return etree.fromstring(content)
    return content.decode("utf-8", errors="replace")


def _http_get_with_retry(
    url: str,
    headers: dict[str, str],
    *,
    timeout: int = 60,
    max_retries: int = 3,
) -> bytes:
    """HTTP GET with exponential backoff retry."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(min(2**attempt, 10))
                continue
            raise LogAPIError(f"HTTP GET failed after {max_retries} attempts: {e}") from e

    raise LogAPIError(f"HTTP GET failed: {last_error}")
