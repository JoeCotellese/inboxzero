"""Layer 3: LLM-based email classifier with dual provider support."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Protocol

import anthropic
import openai

from mailfiler.config import _DEFAULT_CATEGORIES, LabelCategory
from mailfiler.models import Action, LLMClassification

if TYPE_CHECKING:
    from mailfiler.models import EmailMessage

logger = logging.getLogger(__name__)

# Headers that are safe/useful to send to the LLM (full value included)
_FILTERED_HEADERS = {
    "List-Unsubscribe",
    "List-Id",
    "Precedence",
    "Auto-Submitted",
    "Return-Path",
    "Reply-To",
    "X-Mailer",
    "X-Auto-Response-Suppress",
    "X-Campaign-ID",
    "Feedback-ID",
    "X-SG-ID",         # SendGrid
    "X-Mailgun-Tag",   # Mailgun
}

# Prefix-matched headers: include with full value (vendor-specific signals)
_PREFIX_HEADERS = (
    "X-GitHub-",
    "X-JIRA-",
    "X-Slack-",
    "X-PagerDuty-",
    "X-Google-",
)

# Include as "present" only (value is a long cryptographic blob)
_BOOLEAN_HEADERS = {"DKIM-Signature"}

# Include with value
_VALUE_HEADERS = {"Received-SPF", "Authentication-Results"}

_SAFE_DEFAULT = LLMClassification(
    category="unknown",
    priority="medium",
    action=Action.KEEP_INBOX,
    label=None,
    confidence=0.0,
    reason="LLM classification failed or returned low confidence",
)

_SYSTEM_PROMPT = """\
You are a personal assistant triaging your boss's email inbox. Your job is to \
decide what deserves their attention and what can be filed away.

RULES:
- If a real person wrote this email TO or CC'ing your boss, it goes to inbox. \
People matter more than algorithms.
- Business emails with specific recipients (To/CC lists of real people) are \
person-to-person, even if sent from a company domain.
- Newsletters, marketing blasts, automated notifications, and bulk mail get \
labeled and archived. Look for: List-Unsubscribe, Precedence: bulk, ESPs, \
noreply senders, campaign IDs.
- Receipts, shipping confirmations, and security alerts get labeled and archived.
- When in doubt, keep it in inbox. It's better to surface something \
unimportant than to bury something that matters.

Respond with a JSON object only. No preamble, no markdown, no explanation."""

_USER_TEMPLATE = """\
Triage this email for your boss.

From: {display_name} <{from_email}>
To: {to_email}
CC: {cc}
Subject: {subject}
Date: {date}
Headers: {filtered_headers}
Body preview: {body_text}

Available labels (pick one, or null for inbox): {available_labels}

{{
  "category": "person|action_required|fyi|newsletter|marketing|receipt|notification|security|spam",
  "priority": "high|medium|low",
  "action": "keep_inbox|archive|label",
  "label": "<one of the available labels, or null>",
  "confidence": <0.0 to 1.0>,
  "reason": "<one sentence>"
}}"""



class LLMProvider(Protocol):
    """Protocol for LLM providers (Anthropic, Ollama, etc.)."""

    def classify(self, email: EmailMessage) -> LLMClassification:
        """Classify an email using an LLM. Returns structured classification."""
        ...


def build_prompt(
    email: EmailMessage,
    labels_prefix: str = "mailfiler",
    label_categories: list[LabelCategory] | None = None,
) -> str:
    """Construct the user prompt for LLM classification.

    Only includes filtered headers to avoid leaking unnecessary data.
    Passes available labels so the LLM picks from a consistent set.
    When label_categories are provided, includes descriptions for LLM guidance.
    """
    header_parts: list[str] = []
    for key, value in email.headers.items():
        if key in _FILTERED_HEADERS or any(
            key.startswith(prefix) for prefix in _PREFIX_HEADERS
        ):
            header_parts.append(f"{key}: {value}")
        elif key in _BOOLEAN_HEADERS:
            header_parts.append(f"{key}: present")
        elif key in _VALUE_HEADERS:
            header_parts.append(f"{key}: {value}")

    categories = label_categories if label_categories is not None else _DEFAULT_CATEGORIES
    label_parts: list[str] = []
    for cat in categories:
        full_label = f"{labels_prefix}/{cat.name}"
        if cat.description:
            label_parts.append(f"{full_label} ({cat.description})")
        else:
            label_parts.append(full_label)
    available_labels = ", ".join(label_parts)

    cc = email.headers.get("Cc", email.headers.get("CC", "none"))

    return _USER_TEMPLATE.format(
        display_name=email.from_display_name or "",
        from_email=email.from_email,
        to_email=email.to_email,
        cc=cc,
        subject=email.subject or "(no subject)",
        date=email.received_at or "unknown",
        filtered_headers="; ".join(header_parts) if header_parts else "none",
        body_text=email.body_text if email.body_text else "(not available)",
        available_labels=available_labels,
    )


_MARKDOWN_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


class AnthropicLLMProvider:
    """LLM provider using the Anthropic SDK.

    Reads ANTHROPIC_API_KEY from the environment (standard SDK behavior).
    """

    def __init__(
        self,
        *,
        model: str = "claude-haiku-4-5",
        max_tokens: int = 500,
        timeout_seconds: int = 10,
        labels_prefix: str = "mailfiler",
        label_categories: list[LabelCategory] | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._labels_prefix = labels_prefix
        self._label_categories = label_categories
        self._client = anthropic.Anthropic(timeout=float(timeout_seconds))

    def check_health(self) -> tuple[bool, str]:
        """Check that ANTHROPIC_API_KEY is set."""
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY environment variable is not set"
        return True, f"Anthropic ({self._model})"

    def classify(self, email: EmailMessage) -> LLMClassification:
        """Classify an email via the Anthropic API."""
        prompt = build_prompt(
            email,
            labels_prefix=self._labels_prefix,
            label_categories=self._label_categories,
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text
        return _parse_llm_response(raw_text)


def _parse_llm_response(raw_text: str) -> LLMClassification:
    """Parse raw LLM text into an LLMClassification.

    Handles markdown code fences and invalid action values.
    Raises ValueError or KeyError on unparseable responses.
    """
    # Strip markdown fences if present
    fence_match = _MARKDOWN_FENCE_RE.search(raw_text)
    text = fence_match.group(1) if fence_match else raw_text

    data = json.loads(text)

    # Map action string to Action enum, defaulting to KEEP_INBOX for unknown values
    try:
        action = Action(data["action"])
    except ValueError:
        action = Action.KEEP_INBOX

    return LLMClassification(
        category=data["category"],
        priority=data["priority"],
        action=action,
        label=data.get("label"),
        confidence=data["confidence"],
        reason=data.get("reason"),
    )


class LMStudioLLMProvider:
    """LLM provider using LM Studio's OpenAI-compatible API."""

    def __init__(
        self,
        *,
        model: str = "qwen3-30b-a3b-2507",
        base_url: str = "http://localhost:1234/v1",
        max_tokens: int = 500,
        timeout_seconds: int = 30,
        labels_prefix: str = "mailfiler",
        label_categories: list[LabelCategory] | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._labels_prefix = labels_prefix
        self._label_categories = label_categories
        self._base_url = base_url
        self._client = openai.OpenAI(
            base_url=base_url,
            api_key="lm-studio",
            timeout=float(timeout_seconds),
        )

    def check_health(self) -> tuple[bool, str]:
        """Check connectivity to LM Studio by listing models."""
        try:
            self._client.models.list()
        except Exception:
            return False, f"Cannot connect to LM Studio at {self._base_url}"
        return True, f"LM Studio ({self._model})"

    def classify(self, email: EmailMessage) -> LLMClassification:
        """Classify an email via LM Studio."""
        prompt = build_prompt(
            email,
            labels_prefix=self._labels_prefix,
            label_categories=self._label_categories,
        )

        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

        raw_text = response.choices[0].message.content
        return _parse_llm_response(raw_text)


class StubLLMProvider:
    """Placeholder LLM provider that always returns the safe default.

    Use this when no real LLM API key is configured. The pipeline
    falls back to keep_inbox for any email that reaches Layer 3.
    """

    def check_health(self) -> tuple[bool, str]:
        """Stub is always healthy — it doesn't need any external service."""
        return True, "Stub (no LLM, defaults to keep_inbox)"

    def classify(self, email: EmailMessage) -> LLMClassification:
        logger.info("StubLLMProvider: no LLM configured, defaulting to keep_inbox for %s",
                     email.gmail_message_id)
        return _SAFE_DEFAULT


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
