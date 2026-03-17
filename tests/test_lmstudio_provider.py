"""Tests for LMStudioLLMProvider — OpenAI-compatible API against mocked client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from mailfiler.models import Action, EmailMessage, LLMClassification
from mailfiler.pipeline.llm import LLMProvider, LMStudioLLMProvider


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


def _mock_openai_response(content_text: str) -> MagicMock:
    """Build a mock OpenAI chat completions response."""
    message = MagicMock()
    message.content = content_text

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


class TestLMStudioLLMProvider:
    def test_satisfies_protocol(self) -> None:
        """LMStudioLLMProvider should satisfy the LLMProvider protocol."""
        with patch("mailfiler.pipeline.llm.openai.OpenAI"):
            provider: LLMProvider = LMStudioLLMProvider(
                model="local-model",
                base_url="http://localhost:1234/v1",
            )
        assert hasattr(provider, "classify")

    def test_parses_valid_json_response(self) -> None:
        """Should parse a well-formed JSON response into LLMClassification."""
        response_json = json.dumps({
            "category": "newsletter",
            "priority": "low",
            "action": "archive",
            "label": "mailfiler/newsletter",
            "confidence": 0.92,
            "reason": "Marketing newsletter",
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(response_json)

        with patch("mailfiler.pipeline.llm.openai.OpenAI", return_value=mock_client):
            provider = LMStudioLLMProvider(
                model="local-model",
                base_url="http://localhost:1234/v1",
            )

        result = provider.classify(_make_email())

        assert isinstance(result, LLMClassification)
        assert result.action is Action.ARCHIVE
        assert result.label == "mailfiler/newsletter"
        assert result.confidence == 0.92

    def test_calls_openai_sdk_with_correct_params(self) -> None:
        """Should pass model, system prompt, and user prompt to the OpenAI client."""
        response_json = json.dumps({
            "category": "fyi",
            "priority": "low",
            "action": "keep_inbox",
            "label": None,
            "confidence": 0.7,
            "reason": "Informational",
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(response_json)

        with patch("mailfiler.pipeline.llm.openai.OpenAI", return_value=mock_client):
            provider = LMStudioLLMProvider(
                model="qwen2.5-7b",
                base_url="http://localhost:1234/v1",
            )

        email = _make_email()
        provider.classify(email)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "qwen2.5-7b"
        assert len(call_kwargs["messages"]) == 2
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][1]["role"] == "user"
        assert "vendor@newco.io" in call_kwargs["messages"][1]["content"]

    def test_passes_base_url_to_client(self) -> None:
        """Should configure the OpenAI client with the LM Studio base URL."""
        mock_client = MagicMock()

        with patch(
            "mailfiler.pipeline.llm.openai.OpenAI", return_value=mock_client,
        ) as mock_cls:
            LMStudioLLMProvider(
                model="local-model",
                base_url="http://localhost:5555/v1",
            )

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["base_url"] == "http://localhost:5555/v1"

    def test_handles_malformed_json(self) -> None:
        """Should raise on non-JSON response."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            "I cannot classify this email."
        )

        with patch("mailfiler.pipeline.llm.openai.OpenAI", return_value=mock_client):
            provider = LMStudioLLMProvider(
                model="local-model",
                base_url="http://localhost:1234/v1",
            )

        try:
            provider.classify(_make_email())
            assert False, "Should have raised"  # noqa: B011
        except (ValueError, KeyError):
            pass

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
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            f"```json\n{inner_json}\n```"
        )

        with patch("mailfiler.pipeline.llm.openai.OpenAI", return_value=mock_client):
            provider = LMStudioLLMProvider(
                model="local-model",
                base_url="http://localhost:1234/v1",
            )

        result = provider.classify(_make_email())
        assert result.action is Action.ARCHIVE
        assert result.label == "mailfiler/receipts"

    def test_maps_unknown_action_to_keep_inbox(self) -> None:
        """If the LLM returns an unknown action string, default to keep_inbox."""
        response_json = json.dumps({
            "category": "fyi",
            "priority": "medium",
            "action": "yeet_into_void",
            "label": None,
            "confidence": 0.75,
            "reason": "Unknown action test",
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(response_json)

        with patch("mailfiler.pipeline.llm.openai.OpenAI", return_value=mock_client):
            provider = LMStudioLLMProvider(
                model="local-model",
                base_url="http://localhost:1234/v1",
            )

        result = provider.classify(_make_email())
        assert result.action is Action.KEEP_INBOX

    def test_uses_dummy_api_key(self) -> None:
        """LM Studio doesn't need a real API key — should pass a dummy."""
        mock_client = MagicMock()

        with patch(
            "mailfiler.pipeline.llm.openai.OpenAI", return_value=mock_client,
        ) as mock_cls:
            LMStudioLLMProvider(
                model="local-model",
                base_url="http://localhost:1234/v1",
            )

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["api_key"] == "lm-studio"
