"""Domain models and enums for mailfiler."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

class Action(StrEnum):
    """Gmail actions the pipeline can take."""

    ARCHIVE = "archive"
    LABEL = "label"
    KEEP_INBOX = "keep_inbox"
    MARK_READ = "mark_read"
    TRASH = "trash"


class Category(StrEnum):
    """Email sender categories."""

    NEWSLETTER = "newsletter"
    TRANSACTIONAL = "transactional"
    PERSON = "person"
    NOTIFICATION = "notification"
    VIP = "vip"
    UNKNOWN = "unknown"


class DecisionSource(StrEnum):
    """Which pipeline layer made the decision."""

    CACHE_SENDER = "cache:sender"
    CACHE_DOMAIN = "cache:domain"
    HEURISTIC = "heuristic"
    LLM = "llm"
    USER_LEARNED = "user_learned"


@dataclass(frozen=True)
class EmailMessage:
    """Parsed email from Gmail API."""

    gmail_message_id: str
    gmail_thread_id: str | None
    from_email: str
    from_domain: str
    from_display_name: str | None
    to_email: str
    subject: str | None
    snippet: str | None
    headers: dict[str, str]
    received_at: str | None
    body_text: str = ""


@dataclass(frozen=True)
class CacheResult:
    """Result from Layer 1 cache lookup."""

    action: Action
    label: str | None
    confidence: float
    source: DecisionSource
    category: Category


@dataclass(frozen=True)
class HeuristicResult:
    """Result from Layer 2 header heuristics."""

    score: float
    action: Action
    label: str | None
    category: Category
    confidence: float
    applied_rules: list[str]
    is_override: bool


@dataclass(frozen=True)
class LearnedCorrection:
    """A correction detected from user's Gmail label changes."""

    gmail_message_id: str
    from_email: str
    old_action: str
    new_action: str
    old_label: str | None
    new_label: str | None


@dataclass(frozen=True)
class LLMClassification:
    """Parsed response from Layer 3 LLM classifier."""

    category: str
    priority: str
    action: Action
    label: str | None
    confidence: float
    reason: str | None
