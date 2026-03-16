"""Layer 3: LLM-based email classifier with dual provider support."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from mailfiler.models import Action, LLMClassification

if TYPE_CHECKING:
    from mailfiler.models import EmailMessage

logger = logging.getLogger(__name__)

# Headers that are safe/useful to send to the LLM
_FILTERED_HEADERS = {
    "List-Unsubscribe",
    "Precedence",
    "Auto-Submitted",
    "Return-Path",
    "Reply-To",
    "X-Mailer",
}

_BOOLEAN_HEADERS = {"DKIM-Signature"}
_VALUE_HEADERS = {"Received-SPF"}

_SAFE_DEFAULT = LLMClassification(
    category="unknown",
    priority="medium",
    action=Action.KEEP_INBOX,
    label=None,
    confidence=0.0,
    reason="LLM classification failed or returned low confidence",
)

_SYSTEM_PROMPT = (
    "You are an email triage assistant. Classify the email and return a JSON object only. "
    "No preamble, no markdown, no explanation outside the JSON."
)

_USER_TEMPLATE = """\
Classify this email for inbox triage.

From: {display_name} <{from_email}>
To: {to_email}
Subject: {subject}
Date: {date}
Key Headers: {filtered_headers}
Body snippet (first 500 chars): {snippet}

Respond with this exact JSON structure:
{{
  "category": "action_required|reply_needed|fyi|newsletter|receipt|notification|spam",
  "priority": "high|medium|low",
  "action": "keep_inbox|archive|label",
  "label": "<label name or null>",
  "confidence": <0.0 to 1.0>,
  "reason": "<one sentence max>"
}}"""


class LLMProvider(Protocol):
    """Protocol for LLM providers (Anthropic, Ollama, etc.)."""

    def classify(self, email: EmailMessage) -> LLMClassification:
        """Classify an email using an LLM. Returns structured classification."""
        ...


def build_prompt(email: EmailMessage) -> str:
    """Construct the user prompt for LLM classification.

    Only includes filtered headers to avoid leaking unnecessary data.
    """
    header_parts: list[str] = []
    for key, value in email.headers.items():
        if key in _FILTERED_HEADERS:
            header_parts.append(f"{key}: {value}")
        elif key in _BOOLEAN_HEADERS:
            header_parts.append(f"{key}: present")
        elif key in _VALUE_HEADERS:
            header_parts.append(f"{key}: {value}")

    return _USER_TEMPLATE.format(
        display_name=email.from_display_name or "",
        from_email=email.from_email,
        to_email=email.to_email,
        subject=email.subject or "(no subject)",
        date=email.received_at or "unknown",
        filtered_headers="; ".join(header_parts) if header_parts else "none",
        snippet=email.snippet or "",
    )


class LLMLayer:
    """Orchestrates LLM classification with error handling and confidence gating."""

    def __init__(self, *, provider: LLMProvider, llm_threshold: float = 0.6) -> None:
        self._provider = provider
        self._threshold = llm_threshold

    def classify(self, email: EmailMessage) -> LLMClassification:
        """Classify an email via the LLM provider.

        Returns a safe default (keep_inbox, confidence=0.0) on any failure.
        If confidence < threshold, overrides action to keep_inbox.
        """
        try:
            result = self._provider.classify(email)
        except Exception:
            logger.exception("LLM provider failed for %s", email.gmail_message_id)
            return _SAFE_DEFAULT

        # Confidence gating
        if result.confidence < self._threshold:
            return LLMClassification(
                category=result.category,
                priority=result.priority,
                action=Action.KEEP_INBOX,
                label=None,
                confidence=result.confidence,
                reason=result.reason,
            )

        return result
