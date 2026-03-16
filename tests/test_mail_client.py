"""Tests for mail client: FakeMailClient, action mapping, label prefixing."""

from __future__ import annotations

from mailfiler.mail.actions import action_to_label_mods
from mailfiler.models import Action, EmailMessage
from tests.fakes import FakeMailClient


def _make_email(
    gmail_message_id: str = "msg_001",
    subject: str = "Test Subject",
) -> EmailMessage:
    return EmailMessage(
        gmail_message_id=gmail_message_id,
        gmail_thread_id="thread_001",
        from_email="test@example.com",
        from_domain="example.com",
        from_display_name="Test",
        to_email="joe@gmail.com",
        subject=subject,
        snippet="Test body",
        headers={},
        received_at="2026-03-16T09:00:00Z",
    )


class TestFakeMailClient:
    def test_fetch_unread(self) -> None:
        emails = [_make_email(f"msg_{i}") for i in range(5)]
        client = FakeMailClient(emails)
        result = client.fetch_unread(max_results=3)
        assert len(result) == 3

    def test_fetch_unread_empty(self) -> None:
        client = FakeMailClient()
        result = client.fetch_unread(max_results=10)
        assert len(result) == 0

    def test_apply_action_records(self) -> None:
        client = FakeMailClient()
        client.apply_action("msg_001", Action.ARCHIVE, "mailfiler/newsletter")
        assert len(client.applied_actions) == 1
        msg_id, action, label = client.applied_actions[0]
        assert msg_id == "msg_001"
        assert action is Action.ARCHIVE
        assert label == "mailfiler/newsletter"

    def test_search(self) -> None:
        emails = [
            _make_email("msg_1", "Weekly Digest"),
            _make_email("msg_2", "Dinner tonight?"),
            _make_email("msg_3", "Another Digest"),
        ]
        client = FakeMailClient(emails)
        results = client.search("digest")
        assert len(results) == 2

    def test_create_label(self) -> None:
        client = FakeMailClient()
        label_id = client.create_label("mailfiler/newsletter")
        assert label_id.startswith("Label_")
        assert "mailfiler/newsletter" in client.created_labels


class TestActionMapping:
    def test_archive_removes_inbox(self) -> None:
        add, remove = action_to_label_mods(Action.ARCHIVE, "mailfiler/newsletter", "mailfiler")
        assert "INBOX" in remove
        assert "mailfiler/newsletter" in add

    def test_archive_without_label(self) -> None:
        add, remove = action_to_label_mods(Action.ARCHIVE, None, "mailfiler")
        assert "INBOX" in remove
        assert len(add) == 0

    def test_label_adds_label(self) -> None:
        add, remove = action_to_label_mods(Action.LABEL, "mailfiler/github", "mailfiler")
        assert "mailfiler/github" in add
        assert len(remove) == 0

    def test_keep_inbox_is_noop(self) -> None:
        add, remove = action_to_label_mods(Action.KEEP_INBOX, None, "mailfiler")
        assert len(add) == 0
        assert len(remove) == 0

    def test_mark_read_removes_unread(self) -> None:
        _add, remove = action_to_label_mods(Action.MARK_READ, None, "mailfiler")
        assert "UNREAD" in remove

    def test_trash_adds_trash(self) -> None:
        add, _remove = action_to_label_mods(Action.TRASH, None, "mailfiler")
        assert "TRASH" in add


class TestLabelPrefixing:
    def test_labels_are_prefixed(self) -> None:
        """All custom labels should be prefixed with the configured prefix."""
        client = FakeMailClient()
        label_name = "mailfiler/newsletter"
        client.create_label(label_name)
        assert client.created_labels[0].startswith("mailfiler/")


class TestBatchLimits:
    def test_fetch_respects_max_results(self) -> None:
        emails = [_make_email(f"msg_{i}") for i in range(100)]
        client = FakeMailClient(emails)
        result = client.fetch_unread(max_results=50)
        assert len(result) == 50
