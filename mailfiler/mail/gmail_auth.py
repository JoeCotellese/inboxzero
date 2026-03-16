"""Gmail OAuth2 authentication flow.

Placeholder — will be implemented with real OAuth2 in a later phase.
The auth flow requires interactive browser access and real credentials.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
]


def get_gmail_service(credentials_file: Path, token_file: Path) -> object:
    """Authenticate and return a Gmail API service object.

    Args:
        credentials_file: Path to OAuth2 credentials JSON.
        token_file: Path to stored token JSON.

    Returns:
        Gmail API service resource.
    """
    raise NotImplementedError(
        "Real Gmail auth requires credentials.json from Google Cloud Console. "
        "Use FakeMailClient for testing."
    )
