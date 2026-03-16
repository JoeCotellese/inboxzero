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
        self.applied_actions: list[tuple[str, Action, str | None]] = []
        self.created_labels: list[str] = []

    def fetch_unread(self, max_results: int) -> list[EmailMessage]:
        """Return stored emails up to max_results."""
        return self._emails[:max_results]

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
