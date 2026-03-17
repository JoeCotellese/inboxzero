"""Tests for Layer 3: LLM-based email classifier."""

from __future__ import annotations

import json

from mailfiler.models import Action, EmailMessage, LLMClassification
from mailfiler.pipeline.llm import LLMLayer, LLMProvider, build_prompt


def _make_email(
    from_email: str = "vendor@newco.io",
    subject: str = "Partnership opportunity",
    headers: dict[str, str] | None = None,
) -> EmailMessage:
    return EmailMessage(
        gmail_message_id="msg_test",
        gmail_thread_id="thread_test",
        from_email=from_email,
        from_domain=from_email.split("@")[1],
        from_display_name="Vendor",
        to_email="joe@gmail.com",
        subject=subject,
        snippet="We'd love to partner with you on...",
        headers=headers or {},
        received_at="2026-03-16T09:00:00Z",
    )


class FakeLLMProvider:
    """Fake LLM provider for testing."""

    def __init__(self, response: str | None = None, *, raise_error: bool = False) -> None:
        self._response = response
        self._raise_error = raise_error
        self.last_prompt: str | None = None

    def classify(self, email: EmailMessage) -> LLMClassification:
        if self._raise_error:
            msg = "LLM provider timeout"
            raise TimeoutError(msg)
        if self._response is None:
            return LLMClassification(
                category="fyi",
                priority="low",
                action=Action.ARCHIVE,
                label="mailfiler/archived",
                confidence=0.85,
                reason="Low priority informational email",
            )
        # Parse JSON response
        data = json.loads(self._response)
        return LLMClassification(
            category=data["category"],
            priority=data["priority"],
            action=Action(data["action"]),
            label=data.get("label"),
            confidence=data["confidence"],
            reason=data.get("reason"),
        )


class TestBuildPrompt:
    def test_includes_from_and_subject(self) -> None:
        email = _make_email()
        prompt = build_prompt(email)
        assert "vendor@newco.io" in prompt
        assert "Partnership opportunity" in prompt

    def test_includes_available_labels(self) -> None:
        email = _make_email()
        prompt = build_prompt(email)
        assert "mailfiler/newsletter" in prompt
        assert "mailfiler/github" in prompt
        assert "mailfiler/archived" in prompt

    def test_uses_custom_labels_prefix(self) -> None:
        email = _make_email()
        prompt = build_prompt(email, labels_prefix="triage")
        assert "triage/newsletter" in prompt
        assert "triage/github" in prompt

    def test_includes_filtered_headers(self) -> None:
        email = _make_email(headers={
            "List-Unsubscribe": "<mailto:unsub@example.com>",
            "X-Custom-Header": "should-be-excluded",
            "DKIM-Signature": "v=1; a=rsa-sha256",
            "Received-SPF": "pass",
        })
        prompt = build_prompt(email)
        assert "List-Unsubscribe" in prompt
        assert "DKIM-Signature: present" in prompt
        assert "Received-SPF: pass" in prompt
        # Custom headers should not leak
        assert "X-Custom-Header" not in prompt


class TestLLMLayer:
    def test_successful_classification(self) -> None:
        provider = FakeLLMProvider()
        layer = LLMLayer(provider=provider, llm_threshold=0.6)
        result = layer.classify(_make_email())
        assert result.action is Action.ARCHIVE
        assert result.confidence == 0.85

    def test_low_confidence_defaults_to_keep_inbox(self) -> None:
        response = json.dumps({
            "category": "fyi",
            "priority": "low",
            "action": "archive",
            "label": "mailfiler/archived",
            "confidence": 0.4,
            "reason": "Not sure about this one",
        })
        provider = FakeLLMProvider(response=response)
        layer = LLMLayer(provider=provider, llm_threshold=0.6)
        result = layer.classify(_make_email())
        assert result.action is Action.KEEP_INBOX

    def test_provider_timeout_defaults_to_keep_inbox(self) -> None:
        provider = FakeLLMProvider(raise_error=True)
        layer = LLMLayer(provider=provider, llm_threshold=0.6)
        result = layer.classify(_make_email())
        assert result.action is Action.KEEP_INBOX
        assert result.confidence == 0.0

    def test_malformed_json_defaults_to_keep_inbox(self) -> None:
        provider = FakeLLMProvider(response="not json at all")
        layer = LLMLayer(provider=provider, llm_threshold=0.6)
        result = layer.classify(_make_email())
        assert result.action is Action.KEEP_INBOX
        assert result.confidence == 0.0

    def test_missing_fields_defaults_to_keep_inbox(self) -> None:
        response = json.dumps({"category": "fyi"})  # missing most fields
        provider = FakeLLMProvider(response=response)
        layer = LLMLayer(provider=provider, llm_threshold=0.6)
        result = layer.classify(_make_email())
        assert result.action is Action.KEEP_INBOX
        assert result.confidence == 0.0


class TestLLMProviderProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        """FakeLLMProvider should satisfy the LLMProvider protocol."""
        provider: LLMProvider = FakeLLMProvider()
        result = provider.classify(_make_email())
        assert result.action is Action.ARCHIVE
