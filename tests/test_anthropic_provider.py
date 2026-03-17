"""Tests for AnthropicLLMProvider — real Anthropic SDK against mocked API."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from mailfiler.models import Action, EmailMessage, LLMClassification
from mailfiler.pipeline.llm import AnthropicLLMProvider, LLMProvider


def _make_email(
    from_email: str = "vendor@newco.io",
    subject: str = "Partnership opportunity",
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
        headers={},
        received_at="2026-03-16T09:00:00Z",
    )


def _mock_anthropic_response(content_text: str) -> MagicMock:
    """Build a mock Anthropic messages.create() response."""
    content_block = MagicMock()
    content_block.text = content_text

    response = MagicMock()
    response.content = [content_block]
    return response


class TestAnthropicLLMProvider:
    def test_satisfies_protocol(self) -> None:
        """AnthropicLLMProvider should satisfy the LLMProvider protocol."""
        provider: LLMProvider = AnthropicLLMProvider(model="claude-haiku-4-5", max_tokens=500)
        assert hasattr(provider, "classify")

    def test_parses_valid_json_response(self) -> None:
        """Should parse a well-formed JSON response into LLMClassification."""
        response_json = json.dumps({
            "category": "newsletter",
            "priority": "low",
            "action": "archive",
            "label": "mailfiler/newsletter",
            "confidence": 0.92,
            "reason": "Marketing newsletter from known sender",
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(response_json)

        with patch("mailfiler.pipeline.llm.anthropic.Anthropic", return_value=mock_client):
            provider = AnthropicLLMProvider(model="claude-haiku-4-5", max_tokens=500)

        result = provider.classify(_make_email())

        assert isinstance(result, LLMClassification)
        assert result.action is Action.ARCHIVE
        assert result.label == "mailfiler/newsletter"
        assert result.confidence == 0.92
        assert result.category == "newsletter"

    def test_calls_sdk_with_correct_params(self) -> None:
        """Should pass model, max_tokens, system prompt, and user prompt."""
        response_json = json.dumps({
            "category": "fyi",
            "priority": "low",
            "action": "keep_inbox",
            "label": None,
            "confidence": 0.7,
            "reason": "Informational",
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(response_json)

        with patch("mailfiler.pipeline.llm.anthropic.Anthropic", return_value=mock_client):
            provider = AnthropicLLMProvider(model="claude-haiku-4-5", max_tokens=500)

        email = _make_email()
        provider.classify(email)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5"
        assert call_kwargs["max_tokens"] == 500
        assert len(call_kwargs["messages"]) == 1
        assert call_kwargs["messages"][0]["role"] == "user"
        # The prompt should contain the email details
        assert "vendor@newco.io" in call_kwargs["messages"][0]["content"]

    def test_handles_malformed_json(self) -> None:
        """Should raise ValueError on non-JSON response."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(
            "Sorry, I can't classify this email."
        )

        with patch("mailfiler.pipeline.llm.anthropic.Anthropic", return_value=mock_client):
            provider = AnthropicLLMProvider(model="claude-haiku-4-5", max_tokens=500)

        # LLMLayer catches this, but the provider itself should raise
        try:
            provider.classify(_make_email())
            assert False, "Should have raised"  # noqa: B011
        except (ValueError, KeyError):
            pass  # expected

    def test_handles_missing_fields(self) -> None:
        """Should raise on JSON missing required fields."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps({"category": "fyi"})
        )

        with patch("mailfiler.pipeline.llm.anthropic.Anthropic", return_value=mock_client):
            provider = AnthropicLLMProvider(model="claude-haiku-4-5", max_tokens=500)

        try:
            provider.classify(_make_email())
            assert False, "Should have raised"  # noqa: B011
        except KeyError:
            pass  # expected — missing required fields

    def test_strips_markdown_fences(self) -> None:
        """Should handle LLM wrapping JSON in markdown code fences."""
        inner_json = json.dumps({
            "category": "receipt",
            "priority": "low",
            "action": "archive",
            "label": "mailfiler/receipts",
            "confidence": 0.88,
            "reason": "Purchase receipt",
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(
            f"```json\n{inner_json}\n```"
        )

        with patch("mailfiler.pipeline.llm.anthropic.Anthropic", return_value=mock_client):
            provider = AnthropicLLMProvider(model="claude-haiku-4-5", max_tokens=500)

        result = provider.classify(_make_email())
        assert result.action is Action.ARCHIVE
        assert result.label == "mailfiler/receipts"

    def test_maps_unknown_action_to_keep_inbox(self) -> None:
        """If the LLM returns an unknown action string, default to keep_inbox."""
        response_json = json.dumps({
            "category": "fyi",
            "priority": "medium",
            "action": "delete_forever",  # not a valid Action
            "label": None,
            "confidence": 0.75,
            "reason": "Unknown action test",
        })

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(response_json)

        with patch("mailfiler.pipeline.llm.anthropic.Anthropic", return_value=mock_client):
            provider = AnthropicLLMProvider(model="claude-haiku-4-5", max_tokens=500)

        result = provider.classify(_make_email())
        assert result.action is Action.KEEP_INBOX

    def test_uses_timeout(self) -> None:
        """Should pass timeout to the Anthropic client."""
        mock_client = MagicMock()

        with patch(
            "mailfiler.pipeline.llm.anthropic.Anthropic", return_value=mock_client,
        ) as mock_cls:
            AnthropicLLMProvider(model="claude-haiku-4-5", max_tokens=500, timeout_seconds=15)

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["timeout"] == 15.0
