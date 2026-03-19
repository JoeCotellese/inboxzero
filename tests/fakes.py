"""Fake implementations for testing — no real API calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mailfiler.models import Action, EmailMessage


class FakeMailClient:
    """Fake mail client implementing the MailClient Protocol.

    Loads from a list of EmailMessage objects. Tracks actions applied.
    """

    def __init__(self, emails: list[EmailMessage] | None = None) -> None:
        self._emails = list(emails or [])
        self._deleted_ids: set[str] = set()
        self.applied_actions: list[tuple[str, Action, str | None]] = []
        self.created_labels: list[str] = []
        self._message_labels: dict[str, list[str]] = {}
        self.removed_labels: list[tuple[str, str]] = []

    def fetch_unread(self, max_results: int) -> list[EmailMessage]:
        """Return stored emails up to max_results."""
        return self._emails[:max_results]

    def fetch_message(self, message_id: str) -> EmailMessage | None:
        """Look up a message by ID. Returns None for deleted or missing messages."""
        if message_id in self._deleted_ids:
            return None
        for email in self._emails:
            if email.gmail_message_id == message_id:
                return email
        return None

    def fetch_messages(self, message_ids: list[str]) -> dict[str, EmailMessage]:
        """Batch lookup — calls fetch_message for each ID."""
        results: dict[str, EmailMessage] = {}
        for mid in message_ids:
            email = self.fetch_message(mid)
            if email is not None:
                results[mid] = email
        return results

    def delete_message(self, message_id: str) -> None:
        """Test helper: mark a message as deleted."""
        self._deleted_ids.add(message_id)

    def remove_label(self, message_id: str, label_name: str) -> None:
        """Record label removal."""
        self.removed_labels.append((message_id, label_name))

    def apply_action(
        self,
        message_id: str,
        action: Action,
        label: str | None = None,
    ) -> None:
        """Record the action taken."""
        self.applied_actions.append((message_id, action, label))

    def search(self, query: str) -> list[EmailMessage]:
        """Simple substring search on subject."""
        return [e for e in self._emails if query.lower() in (e.subject or "").lower()]

    def create_label(self, label_name: str) -> str:
        """Record label creation and return a fake ID."""
        self.created_labels.append(label_name)
        return f"Label_{len(self.created_labels)}"

    def set_message_labels(self, message_id: str, labels: list[str]) -> None:
        """Test helper: set the labels for a message."""
        self._message_labels[message_id] = list(labels)

    def get_message_labels(self, message_id: str) -> list[str]:
        """Return labels previously set via set_message_labels."""
        return list(self._message_labels.get(message_id, []))
