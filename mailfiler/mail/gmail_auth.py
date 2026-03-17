"""Gmail OAuth2 authentication flow.

Handles token loading, refresh, and interactive browser-based auth.
Requires a credentials.json from Google Cloud Console.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

if TYPE_CHECKING:
    from pathlib import Path

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
]


def get_gmail_service(credentials_file: Path, token_file: Path) -> object:
    """Authenticate and return a Gmail API service object.

    Loads a saved token if available, refreshes it if expired, or runs
    the interactive OAuth2 flow if no valid token exists.

    Args:
        credentials_file: Path to OAuth2 credentials JSON from Google Cloud Console.
        token_file: Path to stored token JSON (created after first auth).

    Returns:
        Gmail API service resource.

    Raises:
        FileNotFoundError: If credentials_file does not exist.
    """
    if not credentials_file.exists():
        msg = f"Gmail credentials file not found: {credentials_file}"
        raise FileNotFoundError(msg)

    creds: Credentials | None = None

    # Try loading existing token
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes=_SCOPES)

    # Refresh or re-authenticate
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes=_SCOPES)
        creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)
