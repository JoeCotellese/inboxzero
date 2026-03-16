"""Gmail API implementation of the MailClient Protocol.

Placeholder — real implementation will be added when OAuth2 is configured.
Tests use FakeMailClient instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mailfiler.models import Action, EmailMessage


class GmailMailClient:
    """Gmail API implementation of the MailClient Protocol.

    Placeholder — requires real OAuth2 credentials to function.
    """

    def __init__(self, service: object, labels_prefix: str = "mailfiler") -> None:
        self._service = service
        self._labels_prefix = labels_prefix

    def fetch_unread(self, max_results: int) -> list[EmailMessage]:
        """Fetch unread emails from Gmail inbox."""
        raise NotImplementedError("Requires Gmail API credentials")

    def apply_action(
        self,
        message_id: str,
        action: Action,
        label: str | None = None,
    ) -> None:
        """Apply a triage action via the Gmail API."""
        raise NotImplementedError("Requires Gmail API credentials")

    def search(self, query: str) -> list[EmailMessage]:
        """Search Gmail with a query string."""
        raise NotImplementedError("Requires Gmail API credentials")

    def create_label(self, label_name: str) -> str:
        """Create a label in Gmail if it doesn't exist."""
        raise NotImplementedError("Requires Gmail API credentials")
