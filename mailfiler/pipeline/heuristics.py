"""Layer 2: Header-based heuristics scoring with ~20 rules and overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mailfiler.models import Action, Category, HeuristicResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from mailfiler.config import AppConfig
    from mailfiler.models import EmailMessage

_BASELINE = 0.5

# Known ESP patterns (case-insensitive matching)
_ESP_PATTERNS = re.compile(
    r"mailchimp|klaviyo|hubspot|marketo|constant\s*contact",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Rule:
    """A scoring rule: condition callable, score delta, and description."""

    condition: Callable[[EmailMessage, AppConfig], bool]
    delta: float
    description: str


def _has_header(name: str) -> Callable[[EmailMessage, AppConfig], bool]:
    """Return a condition that checks if a header exists."""
    def check(email: EmailMessage, config: AppConfig) -> bool:
        return name in email.headers
    return check


def _has_header_prefix(prefix: str) -> Callable[[EmailMessage, AppConfig], bool]:
    """Return a condition that checks if any header starts with prefix."""
    def check(email: EmailMessage, config: AppConfig) -> bool:
        return any(k.startswith(prefix) for k in email.headers)
    return check


def _header_matches(name: str, pattern: str) -> Callable[[EmailMessage, AppConfig], bool]:
    """Return a condition that checks if a header value matches a regex."""
    compiled = re.compile(pattern, re.IGNORECASE)
    def check(email: EmailMessage, config: AppConfig) -> bool:
        val = email.headers.get(name, "")
        return bool(compiled.search(val))
    return check


# Archive signal rules (negative delta)
_ARCHIVE_RULES: list[_Rule] = [
    _Rule(_has_header("List-Unsubscribe"), -0.30, "List-Unsubscribe present"),
    _Rule(
        lambda e, c: email_headers_get(e, "Precedence", "").lower() in ("bulk", "list"),
        -0.25,
        "Precedence: bulk/list",
    ),
    _Rule(
        lambda e, c: email_headers_get(e, "Auto-Submitted", "").lower() in (
            "auto-generated", "auto-replied"
        ),
        -0.35,
        "Auto-Submitted: auto-generated/auto-replied",
    ),
    _Rule(
        lambda e, c: email_headers_get(e, "Return-Path", "") == "<>",
        -0.30,
        "Null Return-Path",
    ),
    _Rule(
        lambda e, c: bool(_ESP_PATTERNS.search(email_headers_get(e, "X-Mailer", ""))),
        -0.25,
        "ESP fingerprint in X-Mailer",
    ),
    _Rule(_has_header("X-Campaign-ID"), -0.20, "X-Campaign-ID present"),
    _Rule(
        _has_header_prefix("X-Mailgun-"),
        -0.15,
        "X-Mailgun-* header present",
    ),
    _Rule(
        _has_header_prefix("X-SendGrid-"),
        -0.15,
        "X-SendGrid-* header present",
    ),
    _Rule(
        _has_header("X-Auto-Response-Suppress"),
        -0.30,
        "X-Auto-Response-Suppress present",
    ),
    _Rule(
        lambda e, c: e.from_email.lower().startswith(("noreply@", "no-reply@", "donotreply@")),
        -0.35,
        "noreply/no-reply/donotreply sender",
    ),
]

# Inbox/trust signal rules (positive delta)
_INBOX_RULES: list[_Rule] = [
    _Rule(
        lambda e, c: (
            bool(c.gmail.user_email)
            and e.to_email.lower() == c.gmail.user_email.lower()
        ),
        +0.30,
        "Direct To: user's primary email",
    ),
    _Rule(
        lambda e, c: e.from_domain.lower() in {d.lower() for d in c.vip_domains.domains},
        +0.40,
        "From VIP domain",
    ),
    _Rule(
        lambda e, c: e.from_email.lower() in {s.lower() for s in c.vip_senders.emails},
        +0.50,
        "From VIP sender",
    ),
    _Rule(
        _has_header("DKIM-Signature"),
        +0.10,
        "DKIM-Signature present",
    ),
    _Rule(
        _header_matches("Received-SPF", r"^pass"),
        +0.10,
        "SPF pass",
    ),
    _Rule(
        lambda e, c: (
            "Reply-To" in e.headers
            and e.headers.get("Reply-To", "").lower() == e.from_email.lower()
        ),
        +0.05,
        "Reply-To matches From",
    ),
]


def email_headers_get(email: EmailMessage, key: str, default: str = "") -> str:
    """Safely get a header value from an email."""
    return email.headers.get(key, default)


def _detect_label(email: EmailMessage, config: AppConfig) -> str:
    """Determine the appropriate label based on header signals."""
    headers = email.headers
    prefix = config.labels.prefix

    # Check specific signals in priority order
    if any(k.startswith("X-GitHub-") for k in headers):
        return f"{prefix}/github"
    if any(k.startswith("X-JIRA-") for k in headers):
        return f"{prefix}/jira"
    if "Auto-Submitted" in headers:
        return f"{prefix}/automated"

    # ESP / marketing signals
    x_mailer = headers.get("X-Mailer", "")
    if _ESP_PATTERNS.search(x_mailer) or "X-Campaign-ID" in headers:
        return f"{prefix}/marketing"

    # Newsletter signals
    if "List-Unsubscribe" in headers or headers.get("Precedence", "").lower() in ("list", "bulk"):
        return f"{prefix}/newsletter"

    return f"{prefix}/archived"


class HeuristicsLayer:
    """Header-based email scoring with override rules."""

    def score(self, email: EmailMessage, config: AppConfig) -> HeuristicResult:
        """Score an email based on header heuristics.

        Override rules are checked first. If matched, scoring is bypassed.
        Otherwise, all archive and inbox rules are evaluated and their deltas
        summed from a baseline of 0.5. The score is clamped to [0.0, 1.0].
        """
        # Check override rules first
        override = self._check_overrides(email, config)
        if override is not None:
            return override

        # Apply scoring rules
        score = _BASELINE
        applied: list[str] = []

        for rule in _ARCHIVE_RULES:
            if rule.condition(email, config):
                score += rule.delta
                applied.append(rule.description)

        for rule in _INBOX_RULES:
            if rule.condition(email, config):
                score += rule.delta
                applied.append(rule.description)

        # Clamp
        score = max(0.0, min(1.0, score))

        # Map score to action
        if score >= 0.85:
            action = Action.ARCHIVE
            label = _detect_label(email, config)
            category = Category.NEWSLETTER  # heuristic default for high archive
            confidence = score
        elif score <= 0.25:
            action = Action.ARCHIVE
            label = _detect_label(email, config)
            category = Category.NEWSLETTER
            confidence = 1.0 - score  # invert: low score = high confidence to archive
        else:
            # Ambiguous — pass to LLM
            action = Action.KEEP_INBOX
            label = None
            category = Category.UNKNOWN
            confidence = abs(score - 0.5) * 2  # distance from midpoint

        return HeuristicResult(
            score=score,
            action=action,
            label=label,
            category=category,
            confidence=confidence,
            applied_rules=applied,
            is_override=False,
        )

    def _check_overrides(
        self, email: EmailMessage, config: AppConfig
    ) -> HeuristicResult | None:
        """Check override rules that bypass scoring entirely."""
        headers = email.headers
        prefix = config.labels.prefix

        # PagerDuty → keep inbox
        if any(k.startswith("X-PagerDuty-") for k in headers):
            return HeuristicResult(
                score=0.0,
                action=Action.KEEP_INBOX,
                label=None,
                category=Category.VIP,
                confidence=1.0,
                applied_rules=["X-PagerDuty override: keep_inbox"],
                is_override=True,
            )

        # GitHub → label
        if any(k.startswith("X-GitHub-") for k in headers):
            return HeuristicResult(
                score=0.0,
                action=Action.LABEL,
                label=f"{prefix}/github",
                category=Category.NOTIFICATION,
                confidence=0.95,
                applied_rules=["X-GitHub override: label:github"],
                is_override=True,
            )

        # JIRA → label
        if any(k.startswith("X-JIRA-") for k in headers):
            return HeuristicResult(
                score=0.0,
                action=Action.LABEL,
                label=f"{prefix}/jira",
                category=Category.NOTIFICATION,
                confidence=0.95,
                applied_rules=["X-JIRA override: label:jira"],
                is_override=True,
            )

        # Slack → archive
        if any(k.startswith("X-Slack-") for k in headers):
            return HeuristicResult(
                score=0.0,
                action=Action.ARCHIVE,
                label=f"{prefix}/archived",
                category=Category.NOTIFICATION,
                confidence=0.95,
                applied_rules=["X-Slack override: archive"],
                is_override=True,
            )

        # Blocked senders
        if email.from_email.lower() in {s.lower() for s in config.blocked_senders.emails}:
            action = Action.TRASH if config.rules.allow_trash else Action.ARCHIVE
            return HeuristicResult(
                score=0.0,
                action=action,
                label=None,
                category=Category.UNKNOWN,
                confidence=1.0,
                applied_rules=["Blocked sender override"],
                is_override=True,
            )

        return None
