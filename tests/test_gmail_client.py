"""Tests for GmailMailClient — real implementation against mocked Gmail API."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

from mailfiler.mail.gmail_client import GmailMailClient
from mailfiler.models import Action, EmailMessage

# --- Gmail API response fixtures ---

def _gmail_message(
    msg_id: str = "abc123",
    thread_id: str = "thread_001",
    from_header: str = "Alice <alice@example.com>",
    to_header: str = "joe@gmail.com",
    subject: str = "Hello World",
    date: str = "Mon, 16 Mar 2026 09:00:00 -0400",
    snippet: str = "Preview text here",
    extra_headers: dict[str, str] | None = None,
    mime_parts: list[dict] | None = None,
) -> dict:
    """Build a Gmail API messages.get() response dict.

    Args:
        mime_parts: Optional MIME parts to add to the payload.
            If provided, payload gets a "parts" key and mimeType "multipart/alternative".
    """
    headers = [
        {"name": "From", "value": from_header},
        {"name": "To", "value": to_header},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": date},
    ]
    if extra_headers:
        for name, value in extra_headers.items():
            headers.append({"name": name, "value": value})

    payload: dict = {"headers": headers}
    if mime_parts:
        payload["mimeType"] = "multipart/alternative"
        payload["parts"] = mime_parts

    return {
        "id": msg_id,
        "threadId": thread_id,
        "snippet": snippet,
        "payload": payload,
    }


def _labels_list_response(*labels: tuple[str, str]) -> dict:
    """Build a Gmail API labels.list() response.

    Each label is a (id, name) tuple.
    """
    return {
        "labels": [{"id": lid, "name": name} for lid, name in labels],
    }


def _mock_service_with_messages(
    messages: list[dict],
    list_response: dict | None = None,
) -> MagicMock:
    """Create a mock Gmail service that returns given messages.

    Args:
        messages: List of Gmail API message dicts (as returned by messages.get).
        list_response: Override for messages.list response. If None, builds from message IDs.
    """
    service = MagicMock()

    if list_response is None:
        list_response = {
            "messages": [{"id": m["id"], "threadId": m.get("threadId")} for m in messages],
        }

    # messages().list().execute()
    service.users().messages().list.return_value.execute.return_value = list_response

    # messages().get().execute() — return each message in order
    get_mock = service.users().messages().get
    get_mock.return_value.execute.side_effect = messages

    return service


class TestFetchUnread:
    def test_returns_email_messages(self) -> None:
        """fetch_unread should parse Gmail API responses into EmailMessage objects."""
        msg = _gmail_message(msg_id="msg_1", from_header="Bob <bob@test.com>", subject="Test")
        service = _mock_service_with_messages([msg])

        client = GmailMailClient(service)
        results = client.fetch_unread(max_results=10)

        assert len(results) == 1
        email = results[0]
        assert isinstance(email, EmailMessage)
        assert email.gmail_message_id == "msg_1"
        assert email.from_email == "bob@test.com"
        assert email.from_domain == "test.com"
        assert email.from_display_name == "Bob"
        assert email.subject == "Test"
        assert email.snippet == "Preview text here"

    def test_respects_max_results(self) -> None:
        """max_results is passed to the Gmail API list call."""
        service = _mock_service_with_messages([], list_response={"messages": []})

        client = GmailMailClient(service)
        client.fetch_unread(max_results=25)

        service.users().messages().list.assert_called_once_with(
            userId="me",
            q="is:unread in:inbox",
            maxResults=25,
        )

    def test_empty_inbox(self) -> None:
        """Returns empty list when no messages match."""
        service = _mock_service_with_messages([], list_response={})

        client = GmailMailClient(service)
        results = client.fetch_unread(max_results=50)

        assert results == []

    def test_parses_bare_email_address(self) -> None:
        """Handles From header with no display name: 'user@example.com'."""
        msg = _gmail_message(from_header="plain@example.com")
        service = _mock_service_with_messages([msg])

        client = GmailMailClient(service)
        results = client.fetch_unread(max_results=10)

        assert results[0].from_email == "plain@example.com"
        assert results[0].from_display_name is None

    def test_parses_quoted_display_name(self) -> None:
        """Handles From header: '"Display Name" <user@example.com>'."""
        msg = _gmail_message(from_header='"Joe Cotellese" <joe@example.com>')
        service = _mock_service_with_messages([msg])

        client = GmailMailClient(service)
        results = client.fetch_unread(max_results=10)

        assert results[0].from_email == "joe@example.com"
        assert results[0].from_display_name == "Joe Cotellese"

    def test_collects_all_headers(self) -> None:
        """All payload headers are stored in the headers dict."""
        msg = _gmail_message(extra_headers={"List-Unsubscribe": "<mailto:unsub@x.com>"})
        service = _mock_service_with_messages([msg])

        client = GmailMailClient(service)
        results = client.fetch_unread(max_results=10)

        assert "List-Unsubscribe" in results[0].headers
        assert results[0].headers["List-Unsubscribe"] == "<mailto:unsub@x.com>"

    def test_multiple_messages(self) -> None:
        """Fetches and parses multiple messages in one batch."""
        msgs = [
            _gmail_message(msg_id="m1", subject="First"),
            _gmail_message(msg_id="m2", subject="Second"),
            _gmail_message(msg_id="m3", subject="Third"),
        ]
        service = _mock_service_with_messages(msgs)

        client = GmailMailClient(service)
        results = client.fetch_unread(max_results=10)

        assert len(results) == 3
        assert [r.subject for r in results] == ["First", "Second", "Third"]

    def test_extracts_body_text_from_mime(self) -> None:
        """Body text is extracted from MIME parts in the payload."""
        body_data = base64.urlsafe_b64encode(b"Hi Joe, let's meet Tuesday.").decode()
        msg = _gmail_message(
            msg_id="body_1",
            mime_parts=[
                {"mimeType": "text/plain", "body": {"data": body_data}},
            ],
        )
        service = _mock_service_with_messages([msg])

        client = GmailMailClient(service)
        results = client.fetch_unread(max_results=10)

        assert results[0].body_text == "Hi Joe, let's meet Tuesday."

    def test_body_text_empty_when_no_mime_body(self) -> None:
        """body_text defaults to empty string when payload has no body parts."""
        msg = _gmail_message(msg_id="nobody_1")
        service = _mock_service_with_messages([msg])

        client = GmailMailClient(service)
        results = client.fetch_unread(max_results=10)

        assert results[0].body_text == ""


class TestApplyAction:
    def test_archive_modifies_labels(self) -> None:
        """ARCHIVE removes INBOX and adds the specified label."""
        service = MagicMock()
        # Set up label resolution
        service.users().labels().list.return_value.execute.return_value = _labels_list_response(
            ("Label_1", "mailfiler/newsletter"),
        )
        service.users().messages().modify.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.ARCHIVE, "mailfiler/newsletter")

        service.users().messages().modify.assert_called_once_with(
            userId="me",
            id="msg_1",
            body={
                "addLabelIds": ["Label_1"],
                "removeLabelIds": ["INBOX"],
            },
        )

    def test_archive_without_label(self) -> None:
        """ARCHIVE with no label just removes INBOX."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response()
        service.users().messages().modify.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.ARCHIVE, None)

        service.users().messages().modify.assert_called_once_with(
            userId="me",
            id="msg_1",
            body={
                "addLabelIds": [],
                "removeLabelIds": ["INBOX"],
            },
        )

    def test_keep_inbox_skips_api_call(self) -> None:
        """KEEP_INBOX is a no-op — should not call messages.modify."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response()

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.KEEP_INBOX, None)

        service.users().messages().modify.assert_not_called()

    def test_mark_read_removes_unread(self) -> None:
        """MARK_READ removes the UNREAD label."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response()
        service.users().messages().modify.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.MARK_READ, None)

        service.users().messages().modify.assert_called_once_with(
            userId="me",
            id="msg_1",
            body={
                "addLabelIds": [],
                "removeLabelIds": ["UNREAD"],
            },
        )

    def test_trash_adds_trash_label(self) -> None:
        """TRASH adds the TRASH system label."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response()
        service.users().messages().modify.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.TRASH, None)

        service.users().messages().modify.assert_called_once_with(
            userId="me",
            id="msg_1",
            body={
                "addLabelIds": ["TRASH"],
                "removeLabelIds": [],
            },
        )


class TestCreateLabel:
    def test_creates_label_returns_id(self) -> None:
        """Creates a new label and returns its ID."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response()
        service.users().labels().create.return_value.execute.return_value = {
            "id": "Label_new",
            "name": "mailfiler/newsletter",
        }

        client = GmailMailClient(service)
        label_id = client.create_label("mailfiler/newsletter")

        assert label_id == "Label_new"
        service.users().labels().create.assert_called_once_with(
            userId="me",
            body={
                "name": "mailfiler/newsletter",
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )

    def test_returns_existing_label_id(self) -> None:
        """If label already exists, return its ID without creating."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response(
            ("Label_existing", "mailfiler/newsletter"),
        )

        client = GmailMailClient(service)
        label_id = client.create_label("mailfiler/newsletter")

        assert label_id == "Label_existing"
        service.users().labels().create.assert_not_called()


class TestSearch:
    def test_search_returns_parsed_messages(self) -> None:
        """search() passes query to Gmail API and returns EmailMessage list."""
        msg = _gmail_message(msg_id="s1", subject="Invoice")
        service = MagicMock()
        service.users().messages().list.return_value.execute.return_value = {
            "messages": [{"id": "s1"}],
        }
        service.users().messages().get.return_value.execute.return_value = msg

        client = GmailMailClient(service)
        results = client.search("from:billing subject:invoice")

        service.users().messages().list.assert_called_once_with(
            userId="me",
            q="from:billing subject:invoice",
            maxResults=100,
        )
        assert len(results) == 1
        assert results[0].subject == "Invoice"

    def test_search_empty_results(self) -> None:
        """search() returns empty list when no messages match."""
        service = MagicMock()
        service.users().messages().list.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        results = client.search("nonexistent query")

        assert results == []

    def test_search_paginates(self) -> None:
        """search() follows nextPageToken to get all results."""
        msg1 = _gmail_message(msg_id="p1", subject="Page1")
        msg2 = _gmail_message(msg_id="p2", subject="Page2")

        service = MagicMock()
        # First page returns one message + nextPageToken
        # Second page returns one message, no token
        list_mock = service.users().messages().list
        list_mock.return_value.execute.side_effect = [
            {"messages": [{"id": "p1"}], "nextPageToken": "token_2"},
            {"messages": [{"id": "p2"}]},
        ]
        get_mock = service.users().messages().get
        get_mock.return_value.execute.side_effect = [msg1, msg2]

        client = GmailMailClient(service)
        results = client.search("test query")

        assert len(results) == 2
        assert results[0].subject == "Page1"
        assert results[1].subject == "Page2"


class TestLabelResolution:
    """Test the internal label name → ID resolution logic."""

    def test_resolves_custom_label_to_id(self) -> None:
        """Custom labels get resolved to their Gmail IDs for modify calls."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response(
            ("Label_42", "mailfiler/receipts"),
        )
        service.users().messages().modify.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.LABEL, "mailfiler/receipts")

        service.users().messages().modify.assert_called_once_with(
            userId="me",
            id="msg_1",
            body={
                "addLabelIds": ["Label_42"],
                "removeLabelIds": [],
            },
        )

    def test_system_labels_not_resolved(self) -> None:
        """System labels (INBOX, UNREAD, TRASH) are used as-is, not resolved."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response()
        service.users().messages().modify.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.ARCHIVE, None)

        # INBOX is a system label and should be passed as "INBOX", not resolved
        modify_body = service.users().messages().modify.call_args.kwargs["body"]
        assert "INBOX" in modify_body["removeLabelIds"]

    def test_caches_label_list(self) -> None:
        """Label list is fetched once and cached for subsequent calls."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response(
            ("L1", "mailfiler/a"),
            ("L2", "mailfiler/b"),
        )
        service.users().messages().modify.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.LABEL, "mailfiler/a")
        client.apply_action("msg_2", Action.LABEL, "mailfiler/b")

        # labels().list() should only be called once
        assert service.users().labels().list.return_value.execute.call_count == 1

    def test_creates_label_on_cache_miss(self) -> None:
        """If label not found in cache, create it and cache the new ID."""
        service = MagicMock()
        service.users().labels().list.return_value.execute.return_value = _labels_list_response()
        service.users().labels().create.return_value.execute.return_value = {
            "id": "Label_auto",
            "name": "mailfiler/auto",
        }
        service.users().messages().modify.return_value.execute.return_value = {}

        client = GmailMailClient(service)
        client.apply_action("msg_1", Action.LABEL, "mailfiler/auto")

        # Should have created the label
        service.users().labels().create.assert_called_once()
        # And used the new ID in the modify call
        modify_body = service.users().messages().modify.call_args.kwargs["body"]
        assert "Label_auto" in modify_body["addLabelIds"]
