"""Mail client Protocol for provider abstraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mailfiler.models import Action, EmailMessage


class MailClient(Protocol):
    """Protocol defining the mail provider interface.

    Any object satisfying this structural type can serve as a mail client.
    Gmail API is the first implementation; IMAP or others can follow.
    """

    def fetch_unread(self, max_results: int) -> list[EmailMessage]:
        """Fetch unread emails from inbox, up to max_results."""
        ...

    def apply_action(
        self,
        message_id: str,
        action: Action,
        label: str | None = None,
    ) -> None:
        """Apply a triage action to a message."""
        ...

    def search(self, query: str) -> list[EmailMessage]:
        """Search for emails matching a Gmail-style query."""
        ...

    def create_label(self, label_name: str) -> str:
        """Create a Gmail label if it doesn't exist. Returns the label ID."""
        ...

    def get_message_labels(self, message_id: str) -> list[str]:
        """Get the label names currently applied to a message."""
        ...
