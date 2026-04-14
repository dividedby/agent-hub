"""Tests for agent-hub router.py"""
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
import requests
import pytest

# Add parent dir so we can import router directly
sys.path.insert(0, str(Path(__file__).parent.parent))
import router


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR, USAGE_FILE, ENV_FILE to tmp paths for isolation."""
    d = tmp_path / "agent-hub"
    d.mkdir()
    monkeypatch.setattr(router, "DATA_DIR", d)
    monkeypatch.setattr(router, "USAGE_FILE", d / "usage.json")
    monkeypatch.setattr(router, "ENV_FILE", d / ".env")
    return d


# ── Usage state tests ─────────────────────────────────────────────────────────

class TestInitUsage:
    def test_all_providers_present(self):
        data = router._init_usage()
        expected = {"groq-fast", "groq-smart", "groq-creative", "cerebras",
                    "gemini-flash", "gemini-lite", "mistral-code"}
        assert set(data.keys()) == expected

    def test_groq_fast_starts_at_zero(self):
        data = router._init_usage()
        assert data["groq-fast"]["requests_used"] == 0
        assert data["groq-fast"]["requests_limit"] == 14400
        assert data["groq-fast"]["reset_window"] == "daily"

    def test_cerebras_starts_at_zero(self):
        data = router._init_usage()
        assert data["cerebras"]["tokens_used"] == 0
        assert data["cerebras"]["tokens_limit"] == 1_000_000

    def test_gemini_flash_starts_at_zero(self):
        data = router._init_usage()
        assert data["gemini-flash"]["requests_used"] == 0
        assert data["gemini-flash"]["requests_limit"] == 250

    def test_gemini_lite_starts_at_zero(self):
        data = router._init_usage()
        assert data["gemini-lite"]["requests_used"] == 0
        assert data["gemini-lite"]["requests_limit"] == 1000

    def test_mistral_code_is_monthly(self):
        data = router._init_usage()
        assert data["mistral-code"]["tokens_limit"] == 4_000_000
        assert data["mistral-code"]["reset_window"] == "monthly"


class TestLoadUsage:
    def test_creates_file_when_missing(self, data_dir):
        assert not (data_dir / "usage.json").exists()
        data = router.load_usage()
        assert (data_dir / "usage.json").exists()
        assert data["groq-fast"]["requests_used"] == 0

    def test_reinitializes_on_malformed_json(self, data_dir):
        (data_dir / "usage.json").write_text("not valid json {{{")
        data = router.load_usage()
        assert data["groq-fast"]["requests_used"] == 0

    def test_reinitializes_when_provider_missing(self, data_dir):
        bad = {"groq-fast": {"requests_used": 5, "requests_limit": 14400,
                             "reset_window": "daily", "last_reset": "2026-03-27T00:00:00+00:00"}}
        (data_dir / "usage.json").write_text(json.dumps(bad))
        data = router.load_usage()
        assert "mistral-code" in data
        assert "gemini-flash" in data
        assert "cerebras" in data

    def test_loads_existing_state(self, data_dir):
        init = router._init_usage()
        init["groq-fast"]["requests_used"] = 1200
        (data_dir / "usage.json").write_text(json.dumps(init))
        data = router.load_usage()
        assert data["groq-fast"]["requests_used"] == 1200


class TestAutoReset:
    def test_daily_resets_when_window_elapsed(self, data_dir):
        data = router._init_usage()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        data["groq-fast"]["requests_used"] = 5000
        data["groq-fast"]["last_reset"] = yesterday.isoformat()
        (data_dir / "usage.json").write_text(json.dumps(data))
        result = router._auto_reset(data)
        assert result["groq-fast"]["requests_used"] == 0

    def test_no_reset_when_window_not_elapsed(self, data_dir):
        data = router._init_usage()
        data["groq-fast"]["requests_used"] = 5000
        (data_dir / "usage.json").write_text(json.dumps(data))
        result = router._auto_reset(data)
        assert result["groq-fast"]["requests_used"] == 5000

    def test_monthly_resets_when_new_month(self, data_dir):
        data = router._init_usage()
        last_month_start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
        last_month_start = last_month_start.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        data["mistral-code"]["tokens_used"] = 2_000_000
        data["mistral-code"]["last_reset"] = last_month_start.isoformat()
        (data_dir / "usage.json").write_text(json.dumps(data))
        result = router._auto_reset(data)
        assert result["mistral-code"]["tokens_used"] == 0

    def test_only_daily_providers_reset_on_new_day(self, data_dir):
        data = router._init_usage()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        data["groq-fast"]["requests_used"] = 5000
        data["groq-fast"]["last_reset"] = yesterday.isoformat()
        data["mistral-code"]["tokens_used"] = 2_000_000  # monthly — should NOT reset
        (data_dir / "usage.json").write_text(json.dumps(data))
        result = router._auto_reset(data)
        assert result["groq-fast"]["requests_used"] == 0
        assert result["mistral-code"]["tokens_used"] == 2_000_000


class TestIncrementUsage:
    def test_increments_request_count(self, data_dir):
        data = router._init_usage()
        (data_dir / "usage.json").write_text(json.dumps(data))
        result = router.increment_usage(data, "groq-fast", 1)
        assert result["groq-fast"]["requests_used"] == 1

    def test_increments_token_count(self, data_dir):
        data = router._init_usage()
        (data_dir / "usage.json").write_text(json.dumps(data))
        result = router.increment_usage(data, "cerebras", 3500)
        assert result["cerebras"]["tokens_used"] == 3500

    def test_persists_to_file(self, data_dir):
        data = router._init_usage()
        (data_dir / "usage.json").write_text(json.dumps(data))
        router.increment_usage(data, "groq-fast", 1)
        saved = json.loads((data_dir / "usage.json").read_text())
        assert saved["groq-fast"]["requests_used"] == 1


# ── Classification and selection tests ────────────────────────────────────────

class TestClassify:
    def test_code_signal_from_keyword(self):
        assert router.classify("write a function to sort a list") == "code"

    def test_code_signal_from_def(self):
        assert router.classify("def foo(): pass — what's wrong?") == "code"

    def test_research_signal(self):
        assert router.classify("explain how transformers work") == "research"

    def test_creative_signal(self):
        assert router.classify("write a story about a dragon") == "creative"

    def test_fast_signal(self):
        assert router.classify("how many days in a leap year") == "fast"

    def test_general_fallback(self):
        assert router.classify("what should I have for lunch") == "general"


class TestSelectProvider:
    def _make_data(self, overrides=None):
        data = router._init_usage()
        if overrides:
            for provider, fields in overrides.items():
                data[provider].update(fields)
        return data

    def test_code_routes_to_mistral(self):
        data = self._make_data()
        provider, warning = router.select_provider(data, "code")
        assert provider == "mistral-code"
        assert warning is None

    def test_research_routes_to_gemini_flash(self):
        data = self._make_data()
        provider, _ = router.select_provider(data, "research")
        assert provider == "gemini-flash"

    def test_creative_routes_to_groq_creative(self):
        data = self._make_data()
        provider, _ = router.select_provider(data, "creative")
        assert provider == "groq-creative"

    def test_fast_routes_to_cerebras(self):
        data = self._make_data()
        provider, _ = router.select_provider(data, "fast")
        assert provider == "cerebras"

    def test_general_routes_to_gemini_lite(self):
        data = self._make_data()
        provider, _ = router.select_provider(data, "general")
        assert provider == "gemini-lite"

    def test_falls_back_when_below_threshold(self):
        # mistral-code at ~91% used (9% remaining) → below 10% threshold → fallback groq-smart
        data = self._make_data({"mistral-code": {"tokens_used": 3_640_001}})
        provider, warning = router.select_provider(data, "code")
        assert provider == "groq-smart"
        assert warning is not None

    def test_falls_back_when_primary_exhausted(self):
        data = self._make_data({"mistral-code": {"tokens_used": 4_000_000}})
        provider, warning = router.select_provider(data, "code")
        assert provider == "groq-smart"
        assert warning is not None

    def test_hard_stop_when_both_exhausted(self):
        data = self._make_data({
            "mistral-code": {"tokens_used": 4_000_000},
            "groq-smart": {"requests_used": 1000},
        })
        with pytest.raises(SystemExit):
            router.select_provider(data, "code")

    def test_exactly_at_threshold_triggers_fallback(self):
        # 10% remaining exactly → should trigger fallback
        data = self._make_data({"mistral-code": {"tokens_used": 3_600_000}})  # 400K/4M = 10% left
        provider, warning = router.select_provider(data, "code")
        assert provider == "groq-smart"
        assert warning is not None


# ── Status bar tests ───────────────────────────────────────────────────────────

class TestStatusBar:
    def _make_data(self):
        return router._init_usage()

    def test_format_count_small_number(self):
        data = self._make_data()
        data["groq-fast"]["requests_used"] = 42
        result = router.format_count(data, "groq-fast")
        assert result == "42/14400"

    def test_format_count_thousands(self):
        data = self._make_data()
        data["groq-fast"]["requests_used"] = 12340
        result = router.format_count(data, "groq-fast")
        assert result == "12K/14400"

    def test_format_count_millions(self):
        data = self._make_data()
        data["cerebras"]["tokens_used"] = 892000
        result = router.format_count(data, "cerebras")
        assert result == "892K/1M"

    def test_indicator_green_when_available(self):
        data = self._make_data()
        data["groq-fast"]["requests_used"] = 0
        assert router._indicator(data, "groq-fast") == "●"

    def test_indicator_gray_when_exhausted(self):
        data = self._make_data()
        data["groq-fast"]["requests_used"] = 14400
        assert router._indicator(data, "groq-fast") == "○"

    def test_status_bar_contains_all_provider_shortcodes(self):
        data = self._make_data()
        bar = router.build_status_bar(data, "groq-fast")
        assert "G/fast" in bar
        assert "G/smart" in bar
        assert "Cbr" in bar
        assert "Gem/flash" in bar
        assert "Mis/code" in bar

    def test_status_bar_fallback_contains_warning(self):
        data = self._make_data()
        bar = router.build_status_bar(data, "groq-fast", warning="⚠ Mistral at limit")
        assert "⚠" in bar
        assert "Mistral" in bar


# ── API call tests ─────────────────────────────────────────────────────────────

def _openai_response(content="hello", tokens=None):
    m = MagicMock()
    payload = {"choices": [{"message": {"content": content}}]}
    if tokens is not None:
        payload["usage"] = {"total_tokens": tokens}
    m.json.return_value = payload
    m.raise_for_status = MagicMock()
    return m


def _gemini_response(content="hello"):
    m = MagicMock()
    m.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": content}]}}],
    }
    m.raise_for_status = MagicMock()
    return m


class TestAPICallGroqFast:
    def test_returns_text_and_one_request(self, data_dir, monkeypatch):
        (data_dir / ".env").write_text("GROQ_API_KEY=test_key\n")
        monkeypatch.setenv("GROQ_API_KEY", "test_key")
        with patch("requests.post", return_value=_openai_response("groq says hi")):
            text, count = router.call_with_retry("groq-fast", "hello")
        assert text == "groq says hi"
        assert count == 1

    def test_raises_when_key_missing(self, data_dir, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        (data_dir / ".env").write_text("")
        with pytest.raises(ValueError, match="GROQ_API_KEY"):
            router.call_with_retry("groq-fast", "hello")


class TestAPICallMistralCode:
    def test_returns_text_and_token_count(self, data_dir, monkeypatch):
        monkeypatch.setattr(router, "_enforce_mistral_rpm", lambda: None)  # skip rate limit
        monkeypatch.setenv("MISTRAL_API_KEY", "test_key")
        (data_dir / ".env").write_text("MISTRAL_API_KEY=test_key\n")
        with patch("requests.post", return_value=_openai_response("mistral says hi", tokens=75)):
            text, tokens = router.call_with_retry("mistral-code", "write a function")
        assert text == "mistral says hi"
        assert tokens == 75

    def test_raises_when_key_missing(self, data_dir, monkeypatch):
        monkeypatch.setattr(router, "_enforce_mistral_rpm", lambda: None)  # skip rate limit
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        (data_dir / ".env").write_text("")
        with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
            router.call_with_retry("mistral-code", "hello")


class TestAPICallGemini:
    def test_returns_text_and_one_request(self, data_dir, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test_key")
        (data_dir / ".env").write_text("GEMINI_API_KEY=test_key\n")
        with patch("requests.post", return_value=_gemini_response("gemini says hi")):
            text, count = router.call_with_retry("gemini-flash", "explain something")
        assert text == "gemini says hi"
        assert count == 1

    def test_raises_when_key_missing(self, data_dir, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        (data_dir / ".env").write_text("")
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            router.call_with_retry("gemini-flash", "hello")


class TestCallWithRetry:
    def test_succeeds_on_first_try(self, data_dir, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test_key")
        (data_dir / ".env").write_text("GROQ_API_KEY=test_key\n")
        with patch("requests.post", return_value=_openai_response("ok")):
            text, count = router.call_with_retry("groq-fast", "hello")
        assert text == "ok"

    def test_retries_once_on_failure_then_succeeds(self, data_dir, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test_key")
        (data_dir / ".env").write_text("GROQ_API_KEY=test_key\n")
        call_count = {"n": 0}

        def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise requests.exceptions.ConnectionError("network error")
            return _openai_response("ok on retry")

        with patch("requests.post", side_effect=flaky):
            with patch("time.sleep"):
                text, _ = router.call_with_retry("groq-fast", "hello")
        assert text == "ok on retry"
        assert call_count["n"] == 2

    def test_raises_if_both_attempts_fail(self, data_dir, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test_key")
        (data_dir / ".env").write_text("GROQ_API_KEY=test_key\n")
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError("down")):
            with patch("time.sleep"):
                with pytest.raises(requests.exceptions.ConnectionError):
                    router.call_with_retry("groq-fast", "hello")


# ── CLI command tests ──────────────────────────────────────────────────────────

class TestCLIStatus:
    def test_prints_all_provider_groups(self, data_dir, capsys):
        data = router._init_usage()
        (data_dir / "usage.json").write_text(json.dumps(data))
        args = MagicMock()
        router.cmd_status(args)
        out = capsys.readouterr().out
        assert "Groq" in out
        assert "Cerebras" in out
        assert "Google" in out
        assert "Mistral" in out


class TestCLIReset:
    def test_resets_groq_fast_to_zero(self, data_dir):
        data = router._init_usage()
        data["groq-fast"]["requests_used"] = 9000
        (data_dir / "usage.json").write_text(json.dumps(data))
        args = MagicMock()
        args.provider = "groq-fast"
        router.cmd_reset(args)
        saved = json.loads((data_dir / "usage.json").read_text())
        assert saved["groq-fast"]["requests_used"] == 0

    def test_resets_mistral_code_to_zero(self, data_dir):
        data = router._init_usage()
        data["mistral-code"]["tokens_used"] = 2_000_000
        (data_dir / "usage.json").write_text(json.dumps(data))
        args = MagicMock()
        args.provider = "mistral-code"
        router.cmd_reset(args)
        saved = json.loads((data_dir / "usage.json").read_text())
        assert saved["mistral-code"]["tokens_used"] == 0


class TestCLISetKey:
    def test_writes_groq_key_to_env(self, data_dir):
        args = MagicMock()
        args.provider = "groq"
        args.value = "gsk_test123"
        router.cmd_set_key(args)
        content = (data_dir / ".env").read_text()
        assert "GROQ_API_KEY=gsk_test123" in content

    def test_writes_cerebras_key(self, data_dir):
        args = MagicMock()
        args.provider = "cerebras"
        args.value = "csk_test456"
        router.cmd_set_key(args)
        content = (data_dir / ".env").read_text()
        assert "CEREBRAS_API_KEY=csk_test456" in content

    def test_writes_mistral_key(self, data_dir):
        args = MagicMock()
        args.provider = "mistral"
        args.value = "mist_test789"
        router.cmd_set_key(args)
        content = (data_dir / ".env").read_text()
        assert "MISTRAL_API_KEY=mist_test789" in content

    def test_preserves_existing_keys(self, data_dir):
        (data_dir / ".env").write_text("GROQ_API_KEY=existing\n")
        args = MagicMock()
        args.provider = "cerebras"
        args.value = "new_key"
        router.cmd_set_key(args)
        content = (data_dir / ".env").read_text()
        assert "GROQ_API_KEY=existing" in content
        assert "CEREBRAS_API_KEY=new_key" in content

    def test_unknown_provider_exits(self, data_dir):
        args = MagicMock()
        args.provider = "unknown-provider"
        args.value = "somekey"
        with pytest.raises(SystemExit):
            router.cmd_set_key(args)


class TestCLIRoute:
    def test_route_calls_provider_and_prints_response(self, data_dir, monkeypatch, capsys):
        data = router._init_usage()
        (data_dir / "usage.json").write_text(json.dumps(data))
        monkeypatch.setenv("GROQ_API_KEY", "test_key")
        (data_dir / ".env").write_text("GROQ_API_KEY=test_key\n")

        with patch.object(router, "call_with_retry", return_value=("response text", 1)):
            args = MagicMock()
            args.task = "quick question"
            args.type = "fast"
            router.cmd_route(args)

        out = capsys.readouterr()
        assert "response text" in out.out

    def test_route_classifies_when_no_type_given(self, data_dir, monkeypatch, capsys):
        data = router._init_usage()
        (data_dir / "usage.json").write_text(json.dumps(data))
        monkeypatch.setenv("GROQ_API_KEY", "test_key")
        (data_dir / ".env").write_text("GROQ_API_KEY=test_key\n")

        with patch.object(router, "call_with_retry", return_value=("classified response", 1)):
            args = MagicMock()
            args.task = "what is machine learning"
            args.type = None
            router.cmd_route(args)

        out = capsys.readouterr()
        assert "classified response" in out.out

    def test_route_falls_back_when_primary_key_missing(self, data_dir, monkeypatch, capsys):
        data = router._init_usage()
        (data_dir / "usage.json").write_text(json.dumps(data))
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setenv("GROQ_API_KEY", "test_key")
        (data_dir / ".env").write_text("GROQ_API_KEY=test_key\n")

        call_counts = {"n": 0}

        def fake_call(provider, task):
            call_counts["n"] += 1
            if provider == "mistral-code":
                raise ValueError("MISTRAL_API_KEY not set")
            return ("fallback response", 1)

        with patch.object(router, "call_with_retry", side_effect=fake_call):
            args = MagicMock()
            args.task = "write a function"
            args.type = "code"
            router.cmd_route(args)

        out = capsys.readouterr()
        assert "fallback response" in out.out

    def test_route_exits_when_both_providers_fail(self, data_dir, monkeypatch, capsys):
        data = router._init_usage()
        (data_dir / "usage.json").write_text(json.dumps(data))
        (data_dir / ".env").write_text("GROQ_API_KEY=test_key\n")

        import requests as req

        def always_fail(provider, task):
            raise req.exceptions.RequestException("network error")

        with patch.object(router, "call_with_retry", side_effect=always_fail):
            args = MagicMock()
            args.task = "quick question"
            args.type = "fast"
            with pytest.raises(SystemExit) as exc_info:
                router.cmd_route(args)

        assert exc_info.value.code == 1
        out = capsys.readouterr()
        assert "failed" in out.err


# ── Rate limit and fallback chain tests ───────────────────────────────────────

class TestMistralRPMSleep:
    def test_enforces_31s_sleep_between_calls(self, monkeypatch):
        """Mistral Codestral 2 RPM requires 31s between calls."""
        sleep_calls = []
        fake_now = 1000.0
        # Patch both sleep and time for fully deterministic control
        monkeypatch.setattr(router.time, "sleep", lambda n: sleep_calls.append(n))
        monkeypatch.setattr(router.time, "time", lambda: fake_now)
        router._last_mistral_call = fake_now - 10  # 10s ago
        router._enforce_mistral_rpm()
        assert len(sleep_calls) == 1
        assert abs(sleep_calls[0] - 21.0) < 0.01

    def test_no_sleep_when_interval_elapsed(self, monkeypatch):
        """No sleep needed if 31s have already passed since last call."""
        sleep_calls = []
        fake_now = 1000.0
        monkeypatch.setattr(router.time, "sleep", lambda n: sleep_calls.append(n))
        monkeypatch.setattr(router.time, "time", lambda: fake_now)
        router._last_mistral_call = fake_now - 35  # 35s ago — past the window
        router._enforce_mistral_rpm()
        assert sleep_calls == []


class TestFallbackChain:
    def test_groq_smart_exhausted_falls_to_gemini_flash(self, data_dir):
        data = router._init_usage()
        data["groq-smart"]["requests_used"] = 1000  # at limit
        provider, warning = router.select_provider(data, "complex")
        assert provider == "gemini-flash"
        assert warning is not None

    def test_cerebras_exhausted_falls_to_groq_fast(self, data_dir):
        data = router._init_usage()
        data["cerebras"]["tokens_used"] = 1_000_000  # at limit
        provider, warning = router.select_provider(data, "fast")
        assert provider == "groq-fast"
        assert warning is not None
