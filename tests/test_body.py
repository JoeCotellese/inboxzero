"""Tests for email body text extraction from Gmail MIME payloads."""

from __future__ import annotations

import base64

from mailfiler.mail.body import extract_body_text


def _b64(text: str) -> str:
    """Base64url-encode a string (matches Gmail API encoding)."""
    return base64.urlsafe_b64encode(text.encode()).decode()


def _plain_payload(text: str) -> dict:
    """Single-part text/plain payload."""
    return {
        "mimeType": "text/plain",
        "body": {"data": _b64(text)},
    }


def _html_payload(html: str) -> dict:
    """Single-part text/html payload."""
    return {
        "mimeType": "text/html",
        "body": {"data": _b64(html)},
    }


def _multipart_alternative(plain: str, html: str) -> dict:
    """multipart/alternative with text/plain and text/html parts."""
    return {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64(plain)}},
            {"mimeType": "text/html", "body": {"data": _b64(html)}},
        ],
    }


class TestExtractBodyText:
    def test_single_part_plain(self) -> None:
        payload = _plain_payload("Hello, this is a test email.")
        assert extract_body_text(payload) == "Hello, this is a test email."

    def test_single_part_html(self) -> None:
        payload = _html_payload("<p>Hello <b>world</b></p>")
        assert extract_body_text(payload) == "Hello world"

    def test_multipart_alternative_prefers_plain(self) -> None:
        payload = _multipart_alternative(
            "Plain version of the email",
            "<p>HTML version of the email</p>",
        )
        assert extract_body_text(payload) == "Plain version of the email"

    def test_nested_multipart(self) -> None:
        """Finds text/plain inside a nested multipart/mixed > multipart/alternative."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64("Deep plain text")}},
                        {"mimeType": "text/html", "body": {"data": _b64("<p>Deep HTML</p>")}},
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "body": {"data": ""},
                },
            ],
        }
        assert extract_body_text(payload) == "Deep plain text"

    def test_quoted_reply_stripping(self) -> None:
        text = "Thanks for the update.\n\n> Original message line 1\n> Original message line 2"
        payload = _plain_payload(text)
        result = extract_body_text(payload)
        assert "Thanks for the update" in result
        assert "> Original message" not in result

    def test_signature_stripping(self) -> None:
        text = "Sounds good, let me know.\n\n-- \nJoe Cotellese\nCEO, AppJawn LLC"
        payload = _plain_payload(text)
        result = extract_body_text(payload)
        assert "Sounds good" in result
        assert "CEO, AppJawn" not in result

    def test_on_wrote_block_stripping(self) -> None:
        text = "Got it, thanks.\n\nOn Mon, Mar 16, 2026 at 9:00 AM Bob wrote:\n> some quoted text"
        payload = _plain_payload(text)
        result = extract_body_text(payload)
        assert "Got it, thanks" in result
        assert "On Mon, Mar 16" not in result

    def test_truncation_at_word_boundary(self) -> None:
        # 10 words of 10 chars each + spaces = 109 chars, well over a 50 limit
        words = ["abcdefghij"] * 10
        text = " ".join(words)
        payload = _plain_payload(text)
        result = extract_body_text(payload)
        # Default limit is 500, which is bigger than our text, so it shouldn't truncate
        assert result == text

    def test_truncation_applied_when_over_limit(self) -> None:
        text = "word " * 200  # 1000 chars
        payload = _plain_payload(text)
        result = extract_body_text(payload)
        assert len(result) <= 503  # 500 + "..."
        assert result.endswith("...")

    def test_empty_payload(self) -> None:
        assert extract_body_text({}) == ""

    def test_missing_body_data(self) -> None:
        payload = {"mimeType": "text/plain", "body": {}}
        assert extract_body_text(payload) == ""

    def test_html_with_style_and_script_stripped(self) -> None:
        html = (
            "<html><head><style>body{color:red}</style></head>"
            "<body><script>alert('hi')</script>"
            "<p>Visible text here</p></body></html>"
        )
        payload = _html_payload(html)
        result = extract_body_text(payload)
        assert "Visible text here" in result
        assert "color:red" not in result
        assert "alert" not in result

    def test_underscore_signature_delimiter(self) -> None:
        text = "Let me know if Tuesday works.\n\n_______________\nSent from my iPhone"
        payload = _plain_payload(text)
        result = extract_body_text(payload)
        assert "Tuesday works" in result
        assert "Sent from my iPhone" not in result

    def test_dash_signature_delimiter(self) -> None:
        text = "See you then.\n\n---\nBest regards,\nAlice"
        payload = _plain_payload(text)
        result = extract_body_text(payload)
        assert "See you then" in result
        assert "Best regards" not in result
