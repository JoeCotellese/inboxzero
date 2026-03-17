"""Tests for Gmail OAuth2 authentication flow."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from mailfiler.mail.gmail_auth import _SCOPES, get_gmail_service


class TestGetGmailService:
    """Tests for get_gmail_service OAuth2 flow."""

    def test_loads_existing_valid_token(self, tmp_path: Path) -> None:
        """When a valid token exists, use it directly without auth flow."""
        creds_file = tmp_path / "credentials.json"
        token_file = tmp_path / "token.json"
        creds_file.write_text("{}")
        token_file.write_text("{}")

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False

        mock_service = MagicMock()

        with (
            patch(
                "mailfiler.mail.gmail_auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ) as mock_from_file,
            patch(
                "mailfiler.mail.gmail_auth.build",
                return_value=mock_service,
            ) as mock_build,
        ):
            service = get_gmail_service(creds_file, token_file)

        mock_from_file.assert_called_once_with(str(token_file), scopes=_SCOPES)
        mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds)
        assert service is mock_service

    def test_refreshes_expired_token(self, tmp_path: Path) -> None:
        """When token exists but is expired with a refresh token, refresh it."""
        creds_file = tmp_path / "credentials.json"
        token_file = tmp_path / "token.json"
        creds_file.write_text("{}")
        token_file.write_text("{}")

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token_123"
        mock_creds.to_json.return_value = '{"token": "refreshed"}'

        mock_service = MagicMock()

        with (
            patch(
                "mailfiler.mail.gmail_auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
            patch("mailfiler.mail.gmail_auth.Request") as mock_request_cls,
            patch(
                "mailfiler.mail.gmail_auth.build",
                return_value=mock_service,
            ),
        ):
            service = get_gmail_service(creds_file, token_file)

        mock_creds.refresh.assert_called_once_with(mock_request_cls())
        assert service is mock_service

    def test_saves_token_after_refresh(self, tmp_path: Path) -> None:
        """After refreshing, the new token is saved to disk."""
        creds_file = tmp_path / "credentials.json"
        token_file = tmp_path / "token.json"
        creds_file.write_text("{}")
        token_file.write_text("{}")

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token_123"
        mock_creds.to_json.return_value = '{"token": "new"}'

        with (
            patch(
                "mailfiler.mail.gmail_auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
            patch("mailfiler.mail.gmail_auth.Request"),
            patch("mailfiler.mail.gmail_auth.build", return_value=MagicMock()),
        ):
            get_gmail_service(creds_file, token_file)

        # Token file should be written with new credentials
        assert token_file.read_text() == '{"token": "new"}'

    def test_runs_auth_flow_when_no_token(self, tmp_path: Path) -> None:
        """When no token file exists, run the interactive OAuth flow."""
        creds_file = tmp_path / "credentials.json"
        token_file = tmp_path / "token.json"
        creds_file.write_text("{}")
        # token_file intentionally does not exist

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.to_json.return_value = '{"token": "brand_new"}'

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds

        mock_service = MagicMock()

        with (
            patch(
                "mailfiler.mail.gmail_auth.InstalledAppFlow.from_client_secrets_file",
                return_value=mock_flow,
            ) as mock_flow_cls,
            patch(
                "mailfiler.mail.gmail_auth.build",
                return_value=mock_service,
            ),
        ):
            service = get_gmail_service(creds_file, token_file)

        mock_flow_cls.assert_called_once_with(str(creds_file), scopes=_SCOPES)
        mock_flow.run_local_server.assert_called_once_with(port=0)
        assert token_file.read_text() == '{"token": "brand_new"}'
        assert service is mock_service

    def test_runs_auth_flow_when_token_invalid_no_refresh(self, tmp_path: Path) -> None:
        """When token exists but is invalid and has no refresh token, re-auth."""
        creds_file = tmp_path / "credentials.json"
        token_file = tmp_path / "token.json"
        creds_file.write_text("{}")
        token_file.write_text("{}")

        # Existing creds are invalid and can't be refreshed
        mock_old_creds = MagicMock()
        mock_old_creds.valid = False
        mock_old_creds.expired = True
        mock_old_creds.refresh_token = None

        mock_new_creds = MagicMock()
        mock_new_creds.valid = True
        mock_new_creds.to_json.return_value = '{"token": "fresh"}'

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_new_creds

        with (
            patch(
                "mailfiler.mail.gmail_auth.Credentials.from_authorized_user_file",
                return_value=mock_old_creds,
            ),
            patch(
                "mailfiler.mail.gmail_auth.InstalledAppFlow.from_client_secrets_file",
                return_value=mock_flow,
            ),
            patch("mailfiler.mail.gmail_auth.build", return_value=MagicMock()),
        ):
            get_gmail_service(creds_file, token_file)

        mock_flow.run_local_server.assert_called_once_with(port=0)

    def test_raises_on_missing_credentials_file(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError if credentials.json doesn't exist."""
        creds_file = tmp_path / "nonexistent.json"
        token_file = tmp_path / "token.json"

        with pytest.raises(FileNotFoundError, match="credentials"):
            get_gmail_service(creds_file, token_file)
