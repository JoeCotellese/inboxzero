"""Configuration loading and validation from TOML files."""

from __future__ import annotations

import tomllib
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path


class RunMode(StrEnum):
    """Controls which pipeline layers execute actions vs. log-only."""

    OBSERVE = "observe"
    HEURISTICS_ONLY = "heuristics_only"
    FULL_AUTO = "full_auto"


class GmailConfig(BaseModel):
    """Gmail API connection settings."""

    credentials_file: str = "~/.mailfiler/credentials.json"
    token_file: str = "~/.mailfiler/token.json"
    user_email: str = ""
    poll_interval_minutes: int = Field(default=5, gt=0)
    max_emails_per_run: int = Field(default=50, gt=0)


class LLMConfig(BaseModel):
    """LLM provider settings."""

    provider: str = "anthropic"
    model: str = ""
    max_tokens: int = Field(default=500, gt=0)
    timeout_seconds: int = Field(default=10, gt=0)
    base_url: str = ""


class RulesConfig(BaseModel):
    """Triage rule thresholds."""

    allow_trash: bool = False
    confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    llm_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    confirmation_days: int = Field(default=7, gt=0)


class VIPSendersConfig(BaseModel):
    """Senders that always stay in inbox."""

    emails: list[str] = Field(default_factory=list)


class VIPDomainsConfig(BaseModel):
    """Domains treated as high-priority."""

    domains: list[str] = Field(default_factory=list)


class BlockedSendersConfig(BaseModel):
    """Senders to trash (requires allow_trash=true)."""

    emails: list[str] = Field(default_factory=list)


class LabelsConfig(BaseModel):
    """Gmail label settings."""

    prefix: str = "mailfiler"


class DatabaseConfig(BaseModel):
    """SQLite database settings."""

    path: str = "~/.mailfiler/mailfiler.db"


class DaemonConfig(BaseModel):
    """Daemon process settings."""

    pid_file: str = "~/.mailfiler/mailfiler.pid"
    run_mode: RunMode = RunMode.OBSERVE


class AppConfig(BaseModel):
    """Top-level application configuration."""

    gmail: GmailConfig = Field(default_factory=GmailConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    vip_senders: VIPSendersConfig = Field(default_factory=VIPSendersConfig)
    vip_domains: VIPDomainsConfig = Field(default_factory=VIPDomainsConfig)
    blocked_senders: BlockedSendersConfig = Field(default_factory=BlockedSendersConfig)
    labels: LabelsConfig = Field(default_factory=LabelsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)


def load_config(path: Path) -> AppConfig:
    """Load and validate configuration from a TOML file.

    Args:
        path: Path to the TOML configuration file.

    Returns:
        Validated AppConfig instance.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If the TOML is invalid or validation fails.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    text = path.read_text()
    data = tomllib.loads(text)
    return AppConfig.model_validate(data)
