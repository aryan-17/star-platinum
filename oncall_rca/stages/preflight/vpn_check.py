"""VPN preflight check — verify log API is reachable before starting a run."""

from __future__ import annotations

import urllib.request
import urllib.error

from oncall_rca.config.settings import Settings


class VPNError(Exception):
    """Base error for VPN/connectivity issues."""


class VPNUnreachable(VPNError):
    """Log API host is not reachable — VPN likely disconnected."""


class VPNAuthError(VPNError):
    """Log API returned 401 — token/user misconfigured."""


def check_vpn(settings: Settings, *, timeout: int = 10) -> None:
    """Verify the log API is reachable.

    Raises:
        VPNUnreachable: If the host cannot be reached (VPN down).
        VPNAuthError: If the API returns 401 (bad token/user config).
    """
    url = f"{settings.log_api.base_url}/air/book?tripId=000000000000&isSRE=true"
    headers = _build_headers(settings)

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Any 2xx means reachable — even if trip doesn't exist
            return
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise VPNAuthError(
                "Log API returned 401. Check LOG_API_TOKEN and LOG_API_USER in .env"
            ) from e
        # Other HTTP errors (404, 500) still mean the API is reachable
        return
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise VPNUnreachable(
            f"Cannot reach log API at {settings.log_api.base_url}. "
            f"Is VPN connected? Error: {e}"
        ) from e


def _build_headers(settings: Settings) -> dict[str, str]:
    """Build HTTP headers for the log API, matching the working curl/script pattern."""
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://statsui.cleartripcorp.me",
        "referer": "https://statsui.cleartripcorp.me/",
        "token": settings.log_api.token or "DummyToken",
        "user": settings.log_api.user or "DummyEmail",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
        ),
    }
