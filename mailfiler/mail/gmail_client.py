"""Gmail API implementation of the MailClient Protocol."""

from __future__ import annotations

import email.utils
import logging
from typing import Any

from mailfiler.mail.actions import action_to_label_mods
from mailfiler.mail.body import extract_body_text
from mailfiler.models import Action, EmailMessage

logger = logging.getLogger(__name__)

# System labels are passed as-is to the Gmail API (no ID resolution needed)
_SYSTEM_LABELS = frozenset({
    "INBOX", "UNREAD", "TRASH", "SPAM", "STARRED",
    "IMPORTANT", "SENT", "DRAFT", "CATEGORY_PERSONAL",
    "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
})


def _parse_from_header(from_header: str) -> tuple[str, str | None]:
    """Parse a From header into (email, display_name).

    Handles formats:
        - "Display Name <user@example.com>"
        - '"Display Name" <user@example.com>'
        - "user@example.com"
    """
    display_name, email_addr = email.utils.parseaddr(from_header)
    if not email_addr:
        # Fallback: treat the whole thing as an email address
        email_addr = from_header.strip()
    return email_addr, display_name or None


def _extract_domain(email_addr: str) -> str:
    """Extract domain from an email address."""
    _, _, domain = email_addr.rpartition("@")
    return domain.lower()


def _get_header(headers: list[dict[str, str]], name: str) -> str | None:
    """Get a header value by name from a Gmail payload headers list."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def _parse_message(raw: dict[str, Any]) -> EmailMessage:
    """Parse a Gmail API message resource into an EmailMessage."""
    payload_headers = raw.get("payload", {}).get("headers", [])

    from_raw = _get_header(payload_headers, "From") or ""
    from_email, from_display_name = _parse_from_header(from_raw)
    from_domain = _extract_domain(from_email)

    to_email = _get_header(payload_headers, "To") or ""
    subject = _get_header(payload_headers, "Subject")
    received_at = _get_header(payload_headers, "Date")

    # Collect all headers into a flat dict
    headers_dict: dict[str, str] = {}
    for h in payload_headers:
        name = h.get("name", "")
        value = h.get("value", "")
        if name:
            headers_dict[name] = value

    body_text = extract_body_text(raw.get("payload", {}))

    return EmailMessage(
        gmail_message_id=raw["id"],
        gmail_thread_id=raw.get("threadId"),
        from_email=from_email,
        from_domain=from_domain,
        from_display_name=from_display_name,
        to_email=to_email,
        subject=subject,
        snippet=raw.get("snippet"),
        headers=headers_dict,
        received_at=received_at,
        body_text=body_text,
    )


class GmailMailClient:
    """Gmail API implementation of the MailClient Protocol."""

    def __init__(self, service: object, labels_prefix: str = "mailfiler") -> None:
        self._service: Any = service
        self._labels_prefix = labels_prefix
        self._label_cache: dict[str, str] | None = None  # name → id

    def fetch_unread(self, max_results: int) -> list[EmailMessage]:
        """Fetch unread emails from Gmail inbox."""
        response = (
            self._service.users()
            .messages()
            .list(userId="me", q="is:unread in:inbox", maxResults=max_results)
            .execute()
        )

        message_stubs = response.get("messages", [])
        if not message_stubs:
            return []

        results: list[EmailMessage] = []
        for stub in message_stubs:
            raw = (
                self._service.users()
                .messages()
                .get(userId="me", id=stub["id"], format="full")
                .execute()
            )
            results.append(_parse_message(raw))

        return results

    def apply_action(
        self,
        message_id: str,
        action: Action,
        label: str | None = None,
    ) -> None:
        """Apply a triage action via the Gmail API."""
        add_names, remove_names = action_to_label_mods(action, label, self._labels_prefix)

        if not add_names and not remove_names:
            return

        # Resolve label names to IDs
        add_ids = [self._resolve_label_id(name) for name in add_names]
        remove_ids = [self._resolve_label_id(name) for name in remove_names]

        self._service.users().messages().modify(
            userId="me",
            id=message_id,
            body={
                "addLabelIds": add_ids,
                "removeLabelIds": remove_ids,
            },
        ).execute()

    def search(self, query: str) -> list[EmailMessage]:
        """Search Gmail with a query string."""
        results: list[EmailMessage] = []
        page_token: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "userId": "me",
                "q": query,
                "maxResults": 100,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            response = self._service.users().messages().list(**kwargs).execute()
            message_stubs = response.get("messages", [])

            for stub in message_stubs:
                raw = (
                    self._service.users()
                    .messages()
                    .get(userId="me", id=stub["id"], format="full")
                    .execute()
                )
                results.append(_parse_message(raw))

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return results

    def create_label(self, label_name: str) -> str:
        """Create a label in Gmail if it doesn't exist. Returns the label ID."""
        label_map = self._get_label_cache()
        if label_name in label_map:
            return label_map[label_name]

        result = (
            self._service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": label_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )

        label_id = result["id"]
        # Update cache
        label_map[label_name] = label_id
        return label_id

    def get_message_labels(self, message_id: str) -> list[str]:
        """Get the label names currently applied to a message.

        Uses format="minimal" for a lightweight API call (returns labelIds only).
        System labels (INBOX, UNREAD, etc.) are returned as-is.
        Custom labels are resolved from ID to name via the label cache.
        """
        response = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="minimal")
            .execute()
        )
        label_ids = response.get("labelIds", [])

        # Build reverse lookup: id → name
        label_cache = self._get_label_cache()
        id_to_name = {lid: name for name, lid in label_cache.items()}

        result: list[str] = []
        for lid in label_ids:
            if lid in _SYSTEM_LABELS:
                result.append(lid)
            elif lid in id_to_name:
                result.append(id_to_name[lid])
            else:
                result.append(lid)
        return result

    # --- Private helpers ---

    def _get_label_cache(self) -> dict[str, str]:
        """Lazy-load and return the label name → ID mapping."""
        if self._label_cache is None:
            response = self._service.users().labels().list(userId="me").execute()
            labels = response.get("labels", [])
            self._label_cache = {lbl["name"]: lbl["id"] for lbl in labels}
        return self._label_cache

    def _resolve_label_id(self, label_name: str) -> str:
        """Resolve a label name to its Gmail ID.

        System labels (INBOX, UNREAD, etc.) are returned as-is.
        Custom labels are looked up in the cache, creating if needed.
        """
        if label_name in _SYSTEM_LABELS:
            return label_name

        label_map = self._get_label_cache()
        if label_name in label_map:
            return label_map[label_name]

        # Label doesn't exist yet — create it
        return self.create_label(label_name)
