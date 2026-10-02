"""Tests for VPN preflight check."""

from unittest.mock import patch, MagicMock
import urllib.error

import pytest

from oncall_rca.config.settings import Settings
from oncall_rca.stages.preflight.vpn_check import (
    VPNAuthError,
    VPNUnreachable,
    check_vpn,
    _build_headers,
)


@pytest.fixture
def settings() -> Settings:
    return Settings()


class TestBuildHeaders:
    def test_default_headers(self, settings: Settings) -> None:
        headers = _build_headers(settings)
        assert headers["origin"] == "https://statsui.cleartripcorp.me"
        assert headers["token"] == "DummyToken"
        assert headers["user"] == "DummyEmail"


class TestCheckVPN:
    def test_reachable(self, settings: Settings) -> None:
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("oncall_rca.stages.preflight.vpn_check.urllib.request.urlopen", return_value=mock_resp):
            check_vpn(settings)  # should not raise

    def test_vpn_down(self, settings: Settings) -> None:
        with patch(
            "oncall_rca.stages.preflight.vpn_check.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises(VPNUnreachable, match="VPN connected"):
                check_vpn(settings)

    def test_auth_error(self, settings: Settings) -> None:
        with patch(
            "oncall_rca.stages.preflight.vpn_check.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="", code=401, msg="Unauthorized", hdrs=None, fp=None  # type: ignore[arg-type]
            ),
        ):
            with pytest.raises(VPNAuthError, match="401"):
                check_vpn(settings)

    def test_server_error_still_reachable(self, settings: Settings) -> None:
        with patch(
            "oncall_rca.stages.preflight.vpn_check.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="", code=500, msg="Server Error", hdrs=None, fp=None  # type: ignore[arg-type]
            ),
        ):
            check_vpn(settings)  # 500 = reachable, should not raise
