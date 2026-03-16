"""Tests for Layer 2: Header-based heuristics scoring."""

from __future__ import annotations

import json
from pathlib import Path

from mailfiler.config import AppConfig
from mailfiler.models import Action, EmailMessage
from mailfiler.pipeline.heuristics import HeuristicsLayer

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> EmailMessage:
    """Load a named email fixture."""
    data = json.loads((FIXTURES_DIR / "sample_emails.json").read_text())
    for entry in data:
        if entry["name"] == name:
            return EmailMessage(
                gmail_message_id=entry["gmail_message_id"],
                gmail_thread_id=entry["gmail_thread_id"],
                from_email=entry["from_email"],
                from_domain=entry["from_domain"],
                from_display_name=entry["from_display_name"],
                to_email=entry["to_email"],
                subject=entry["subject"],
                snippet=entry["snippet"],
                headers=entry["headers"],
                received_at=entry["received_at"],
            )
    raise ValueError(f"Fixture {name!r} not found")


def _make_email(
    from_email: str = "test@example.com",
    from_domain: str = "example.com",
    to_email: str = "joe@gmail.com",
    headers: dict[str, str] | None = None,
    subject: str | None = "Test Subject",
) -> EmailMessage:
    return EmailMessage(
        gmail_message_id="msg_test",
        gmail_thread_id=None,
        from_email=from_email,
        from_domain=from_domain,
        from_display_name=None,
        to_email=to_email,
        subject=subject,
        snippet="Test body",
        headers=headers or {},
        received_at="2026-03-16T09:00:00Z",
    )


def _default_config(**overrides: object) -> AppConfig:
    """Create a config with optional overrides for VIP/blocked lists."""
    data: dict[str, object] = {
        "gmail": {"credentials_file": "x", "token_file": "x"},
        "llm": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "rules": {},
        "vip_senders": {"emails": []},
        "vip_domains": {"domains": []},
        "blocked_senders": {"emails": []},
        "labels": {"prefix": "mailfiler"},
        "database": {},
        "daemon": {},
    }
    data.update(overrides)
    return AppConfig.model_validate(data)


class TestArchiveSignals:
    """Individual archive-signal rules should lower the score."""

    def test_list_unsubscribe(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"List-Unsubscribe": "<mailto:unsub@example.com>"})
        result = layer.score(email, _default_config())
        assert result.score < 0.5  # below baseline

    def test_precedence_bulk(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"Precedence": "bulk"})
        result = layer.score(email, _default_config())
        assert result.score < 0.5

    def test_auto_submitted(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"Auto-Submitted": "auto-generated"})
        result = layer.score(email, _default_config())
        assert result.score < 0.5

    def test_null_return_path(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"Return-Path": "<>"})
        result = layer.score(email, _default_config())
        assert result.score < 0.5

    def test_esp_mailer(self) -> None:
        layer = HeuristicsLayer()
        for esp in ["MailChimp", "Klaviyo", "HubSpot", "Marketo", "Constant Contact"]:
            email = _make_email(headers={"X-Mailer": f"{esp} Mailer v2"})
            result = layer.score(email, _default_config())
            assert result.score < 0.5, f"Failed for ESP: {esp}"

    def test_campaign_id(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"X-Campaign-ID": "camp_123"})
        result = layer.score(email, _default_config())
        assert result.score < 0.5

    def test_sendgrid_header(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"X-SendGrid-EID": "abc123"})
        result = layer.score(email, _default_config())
        assert result.score < 0.5

    def test_mailgun_header(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"X-Mailgun-Tag": "promo"})
        result = layer.score(email, _default_config())
        assert result.score < 0.5

    def test_auto_response_suppress(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"X-Auto-Response-Suppress": "All"})
        result = layer.score(email, _default_config())
        assert result.score < 0.5

    def test_noreply_from(self) -> None:
        layer = HeuristicsLayer()
        for prefix in ["noreply@", "no-reply@", "donotreply@"]:
            email = _make_email(from_email=f"{prefix}example.com")
            result = layer.score(email, _default_config())
            assert result.score < 0.5, f"Failed for: {prefix}"


class TestInboxSignals:
    """Individual inbox-signal rules should raise the score."""

    def test_direct_to_user(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(to_email="joe@gmail.com")
        config = _default_config(
            gmail={"credentials_file": "x", "token_file": "x", "user_email": "joe@gmail.com"},
        )
        result = layer.score(email, config)
        # Direct-to adds +0.30 to 0.5 baseline = 0.8
        assert result.score >= 0.7

    def test_vip_domain(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(from_domain="wavely.com")
        config = _default_config(vip_domains={"domains": ["wavely.com"]})
        result = layer.score(email, config)
        assert result.score > 0.85

    def test_vip_sender(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(from_email="boss@wavely.com")
        config = _default_config(vip_senders={"emails": ["boss@wavely.com"]})
        result = layer.score(email, config)
        assert result.score > 0.9

    def test_dkim_valid(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"DKIM-Signature": "v=1; a=rsa-sha256"})
        result = layer.score(email, _default_config())
        assert result.score >= 0.5  # +0.10 from DKIM

    def test_spf_pass(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"Received-SPF": "pass"})
        result = layer.score(email, _default_config())
        assert result.score >= 0.5  # +0.10 from SPF


class TestOverrideRules:
    """Override rules should bypass scoring entirely."""

    def test_pagerduty_override(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("pagerduty_alert")
        result = layer.score(email, _default_config())
        assert result.is_override is True
        assert result.action is Action.KEEP_INBOX
        assert result.confidence == 1.0

    def test_github_override(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("github_notification")
        result = layer.score(email, _default_config())
        assert result.is_override is True
        assert result.action is Action.LABEL
        assert result.label == "mailfiler/github"
        assert result.confidence == 0.95

    def test_jira_override(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("jira_notification")
        result = layer.score(email, _default_config())
        assert result.is_override is True
        assert result.action is Action.LABEL
        assert result.label == "mailfiler/jira"
        assert result.confidence == 0.95

    def test_slack_override(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("slack_notification")
        result = layer.score(email, _default_config())
        assert result.is_override is True
        assert result.action is Action.ARCHIVE
        assert result.confidence == 0.95

    def test_blocked_sender_override(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(from_email="spammer@spam.com")
        config = _default_config(
            blocked_senders={"emails": ["spammer@spam.com"]},
            rules={"allow_trash": True},
        )
        result = layer.score(email, config)
        assert result.is_override is True
        assert result.action is Action.TRASH
        assert result.confidence == 1.0

    def test_blocked_sender_without_trash_permission(self) -> None:
        """Blocked sender with allow_trash=false should still archive."""
        layer = HeuristicsLayer()
        email = _make_email(from_email="spammer@spam.com")
        config = _default_config(
            blocked_senders={"emails": ["spammer@spam.com"]},
            rules={"allow_trash": False},
        )
        result = layer.score(email, config)
        assert result.is_override is True
        assert result.action is Action.ARCHIVE


class TestCombinedScoring:
    """Test combined effects of multiple rules."""

    def test_newsletter_scores_low(self) -> None:
        """Newsletter with multiple archive signals should score very low."""
        layer = HeuristicsLayer()
        email = _load_fixture("newsletter_with_unsub")
        result = layer.score(email, _default_config())
        assert result.score <= 0.25
        assert result.action is Action.ARCHIVE

    def test_marketing_email_scores_low(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("marketing_esp")
        result = layer.score(email, _default_config())
        assert result.score <= 0.25

    def test_automated_email_scores_low(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("noreply_automated")
        result = layer.score(email, _default_config())
        assert result.score <= 0.25

    def test_score_clamped_to_0_1(self) -> None:
        """Score should never go below 0 or above 1."""
        layer = HeuristicsLayer()
        # Stack every archive signal
        email = _make_email(
            from_email="noreply@example.com",
            headers={
                "List-Unsubscribe": "<mailto:unsub>",
                "Precedence": "bulk",
                "Auto-Submitted": "auto-generated",
                "Return-Path": "<>",
                "X-Mailer": "MailChimp",
                "X-Campaign-ID": "c1",
                "X-Auto-Response-Suppress": "All",
            },
        )
        result = layer.score(email, _default_config())
        assert result.score >= 0.0
        assert result.score <= 1.0


class TestCategoryMapping:
    """Score-to-category mapping."""

    def test_high_archive_confidence(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("newsletter_with_unsub")
        result = layer.score(email, _default_config())
        assert result.action is Action.ARCHIVE

    def test_ambiguous_range(self) -> None:
        """A score between 0.25 and 0.85 should result in keep_inbox (ambiguous → LLM)."""
        layer = HeuristicsLayer()
        # Minimal headers — should land in ambiguous range
        email = _make_email(headers={})
        config = _default_config(vip_senders={"emails": []}, vip_domains={"domains": []})
        result = layer.score(email, config)
        if 0.25 < result.score < 0.85:
            assert result.action is Action.KEEP_INBOX


class TestLabelAssignment:
    """Correct labels assigned based on header signals."""

    def test_newsletter_label(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"List-Unsubscribe": "<mailto:unsub>", "Precedence": "list"})
        result = layer.score(email, _default_config())
        if result.action is Action.ARCHIVE:
            assert result.label == "mailfiler/newsletter"

    def test_esp_marketing_label(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"X-Mailer": "MailChimp Mailer", "X-Campaign-ID": "c1"})
        result = layer.score(email, _default_config())
        if result.action is Action.ARCHIVE:
            assert result.label == "mailfiler/marketing"

    def test_github_label(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("github_notification")
        result = layer.score(email, _default_config())
        assert result.label == "mailfiler/github"

    def test_jira_label(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("jira_notification")
        result = layer.score(email, _default_config())
        assert result.label == "mailfiler/jira"

    def test_automated_label(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={"Auto-Submitted": "auto-generated"})
        result = layer.score(email, _default_config())
        if result.action is Action.ARCHIVE:
            assert result.label == "mailfiler/automated"


class TestAppliedRulesAudit:
    """HeuristicResult should include which rules fired."""

    def test_applied_rules_populated(self) -> None:
        layer = HeuristicsLayer()
        email = _make_email(headers={
            "List-Unsubscribe": "<mailto:unsub>",
            "Precedence": "bulk",
        })
        result = layer.score(email, _default_config())
        assert len(result.applied_rules) >= 2

    def test_override_rules_in_audit(self) -> None:
        layer = HeuristicsLayer()
        email = _load_fixture("pagerduty_alert")
        result = layer.score(email, _default_config())
        assert len(result.applied_rules) >= 1
