"""Tests for config loading and validation."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from mailfiler.config import AppConfig, RunMode, load_config

if TYPE_CHECKING:
    from pathlib import Path


class TestRunMode:
    def test_enum_values(self) -> None:
        assert RunMode.OBSERVE.value == "observe"
        assert RunMode.HEURISTICS_ONLY.value == "heuristics_only"
        assert RunMode.FULL_AUTO.value == "full_auto"

    def test_from_string(self) -> None:
        assert RunMode("observe") is RunMode.OBSERVE
        assert RunMode("full_auto") is RunMode.FULL_AUTO


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_config: Path) -> None:
        config = load_config(tmp_config)
        assert isinstance(config, AppConfig)

    def test_gmail_defaults(self, tmp_config: Path) -> None:
        config = load_config(tmp_config)
        assert config.gmail.poll_interval_minutes == 5
        assert config.gmail.max_emails_per_run == 50

    def test_llm_defaults(self, tmp_config: Path) -> None:
        config = load_config(tmp_config)
        assert config.llm.provider == "anthropic"
        assert config.llm.model == "claude-haiku-4-5"
        assert config.llm.max_tokens == 500
        assert config.llm.timeout_seconds == 10

    def test_rules_defaults(self, tmp_config: Path) -> None:
        config = load_config(tmp_config)
        assert config.rules.allow_trash is False
        assert config.rules.confidence_threshold == 0.85
        assert config.rules.llm_threshold == 0.6
        assert config.rules.confirmation_days == 7

    def test_run_mode_from_config(self, tmp_config: Path) -> None:
        config = load_config(tmp_config)
        assert config.daemon.run_mode is RunMode.OBSERVE

    def test_labels_prefix(self, tmp_config: Path) -> None:
        config = load_config(tmp_config)
        assert config.labels.prefix == "mailfiler"

    def test_vip_lists_empty_by_default(self, tmp_config: Path) -> None:
        config = load_config(tmp_config)
        assert config.vip_senders.emails == []
        assert config.vip_domains.domains == []
        assert config.blocked_senders.emails == []


class TestConfigValidation:
    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.toml")

    def test_rejects_invalid_toml(self, tmp_path: Path) -> None:
        bad_config = tmp_path / "bad.toml"
        bad_config.write_text("this is not [valid toml")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_config(bad_config)

    def test_rejects_invalid_run_mode(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("""\
[gmail]
credentials_file = "x"
token_file = "x"

[llm]
provider = "anthropic"
model = "claude-haiku-4-5"

[rules]

[vip_senders]
emails = []

[vip_domains]
domains = []

[blocked_senders]
emails = []

[labels]
prefix = "mailfiler"

[database]
path = "/tmp/test.db"

[daemon]
pid_file = "/tmp/test.pid"
run_mode = "invalid_mode"
""")
        with pytest.raises(ValidationError):
            load_config(config_path)

    def test_rejects_negative_poll_interval(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("""\
[gmail]
credentials_file = "x"
token_file = "x"
poll_interval_minutes = -1

[llm]
provider = "anthropic"
model = "claude-haiku-4-5"

[rules]

[vip_senders]
emails = []

[vip_domains]
domains = []

[blocked_senders]
emails = []

[labels]
prefix = "mailfiler"

[database]
path = "/tmp/test.db"

[daemon]
pid_file = "/tmp/test.pid"
run_mode = "observe"
""")
        with pytest.raises(ValidationError):
            load_config(config_path)

    def test_rejects_confidence_out_of_range(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("""\
[gmail]
credentials_file = "x"
token_file = "x"

[llm]
provider = "anthropic"
model = "claude-haiku-4-5"

[rules]
confidence_threshold = 1.5

[vip_senders]
emails = []

[vip_domains]
domains = []

[blocked_senders]
emails = []

[labels]
prefix = "mailfiler"

[database]
path = "/tmp/test.db"

[daemon]
pid_file = "/tmp/test.pid"
run_mode = "observe"
""")
        with pytest.raises(ValidationError):
            load_config(config_path)


class TestConfigDefaults:
    def test_minimal_config_uses_defaults(self, tmp_path: Path) -> None:
        """Config with only required sections should fill in defaults."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("""\
[gmail]
credentials_file = "~/.mailfiler/credentials.json"
token_file = "~/.mailfiler/token.json"

[llm]
provider = "anthropic"
model = "claude-haiku-4-5"

[rules]

[vip_senders]
emails = []

[vip_domains]
domains = []

[blocked_senders]
emails = []

[labels]

[database]

[daemon]
""")
        config = load_config(config_path)
        assert config.gmail.poll_interval_minutes == 5
        assert config.gmail.max_emails_per_run == 50
        assert config.llm.max_tokens == 500
        assert config.llm.timeout_seconds == 10
        assert config.rules.allow_trash is False
        assert config.rules.confidence_threshold == 0.85
        assert config.rules.llm_threshold == 0.6
        assert config.rules.confirmation_days == 7
        assert config.labels.prefix == "mailfiler"
        assert config.daemon.run_mode is RunMode.OBSERVE
