"""Tests for configuration loading."""

from oncall_rca.config.settings import Settings, load_settings


class TestSettings:
    """Settings load from defaults without any env vars."""

    def test_default_settings_load(self) -> None:
        settings = Settings()
        assert settings.log_api.base_url == "https://bqapi.cleartripcorp.me/bqAPI"
        assert settings.log_api.token == ""
        assert settings.gmail.poll_interval_seconds == 60
        assert settings.budgets.investigator_max_iterations == 10
        assert settings.budgets.tokens_per_stage_limit == 50_000
        assert settings.model.model == "openai/gpt-oss-120b"
        assert settings.output.rca_dir.name == "rca_reports"
        assert settings.output.cache_dir.name == "cache"

    def test_load_settings_function(self) -> None:
        settings = load_settings()
        assert isinstance(settings, Settings)

    def test_env_override(self, monkeypatch: object) -> None:
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("LOG_API_TOKEN", "test-token-123")
        mp.setenv("GROQ_MODEL", "qwen/qwen3.8-27b")
        mp.setenv("INVESTIGATOR_MAX_ITERATIONS", "20")

        from oncall_rca.config.settings import LogApiSettings, ModelSettings, BudgetSettings

        assert LogApiSettings().token == "test-token-123"
        assert ModelSettings().model == "qwen/qwen3.8-27b"
        assert BudgetSettings().investigator_max_iterations == 20

        mp.undo()
