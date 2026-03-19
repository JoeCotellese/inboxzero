"""Tests for Layer 3: LLM-based email classifier."""

from __future__ import annotations

import json

import pytest  # noqa: TC002 — used as fixture type at runtime

from mailfiler.config import LabelCategory
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
                label="mailfiler/marketing",
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
        assert "mailfiler/records" in prompt
        assert "mailfiler/marketing" in prompt

    def test_uses_custom_labels_prefix(self) -> None:
        email = _make_email()
        prompt = build_prompt(email, labels_prefix="triage")
        assert "triage/newsletter" in prompt
        assert "triage/records" in prompt

    def test_includes_body_text(self) -> None:
        email = _make_email()
        email_with_body = EmailMessage(
            gmail_message_id=email.gmail_message_id,
            gmail_thread_id=email.gmail_thread_id,
            from_email=email.from_email,
            from_domain=email.from_domain,
            from_display_name=email.from_display_name,
            to_email=email.to_email,
            subject=email.subject,
            snippet=email.snippet,
            headers=email.headers,
            received_at=email.received_at,
            body_text="Hi Joe, wanted to follow up on our meeting.",
        )
        prompt = build_prompt(email_with_body)
        assert "Hi Joe, wanted to follow up on our meeting." in prompt

    def test_empty_body_text_shows_not_available(self) -> None:
        email = _make_email()
        prompt = build_prompt(email)
        assert "(not available)" in prompt

    def test_includes_descriptions_when_categories_provided(self) -> None:
        email = _make_email()
        categories = [
            LabelCategory(name="inbox", description="Important emails"),
            LabelCategory(name="marketing", description="Promos and bulk"),
            LabelCategory(name="travel", description="Travel bookings"),
        ]
        prompt = build_prompt(email, label_categories=categories)
        assert "mailfiler/travel (Travel bookings)" in prompt
        assert "mailfiler/inbox (Important emails)" in prompt

    def test_custom_label_appears_in_prompt(self) -> None:
        email = _make_email()
        categories = [
            LabelCategory(name="inbox"),
            LabelCategory(name="finance", description="Financial emails"),
        ]
        prompt = build_prompt(email, label_categories=categories)
        assert "mailfiler/finance" in prompt
        # Default labels should NOT appear
        assert "mailfiler/newsletter" not in prompt

    def test_no_categories_uses_defaults(self) -> None:
        email = _make_email()
        prompt = build_prompt(email)
        assert "mailfiler/newsletter" in prompt
        assert "mailfiler/records" in prompt
        assert "mailfiler/marketing" in prompt

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
            "label": "mailfiler/marketing",
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


class TestHealthCheck:
    def test_stub_always_healthy(self) -> None:
        from mailfiler.pipeline.llm import StubLLMProvider

        provider = StubLLMProvider()
        ok, _msg = provider.check_health()
        assert ok is True

    def test_anthropic_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mailfiler.pipeline.llm import AnthropicLLMProvider

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        provider = AnthropicLLMProvider()
        ok, msg = provider.check_health()
        assert ok is False
        assert "ANTHROPIC_API_KEY" in msg

    def test_anthropic_has_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mailfiler.pipeline.llm import AnthropicLLMProvider

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        provider = AnthropicLLMProvider()
        ok, _msg = provider.check_health()
        assert ok is True

    def test_lmstudio_unreachable(self) -> None:
        from mailfiler.pipeline.llm import LMStudioLLMProvider

        # Use a port that's almost certainly not running anything
        provider = LMStudioLLMProvider(base_url="http://127.0.0.1:19999/v1", timeout_seconds=2)
        ok, msg = provider.check_health()
        assert ok is False
        assert "127.0.0.1:19999" in msg or "connect" in msg.lower()


class TestLLMProviderProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        """FakeLLMProvider should satisfy the LLMProvider protocol."""
        provider: LLMProvider = FakeLLMProvider()
        result = provider.classify(_make_email())
        assert result.action is Action.ARCHIVE
