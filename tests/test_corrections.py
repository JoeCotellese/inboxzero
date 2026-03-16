"""Tests for feedback loop: correction detection and confidence adjustment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailfiler.db.queries import get_sender_profile, upsert_sender_profile
from mailfiler.db.schema import initialize_db
from mailfiler.feedback.corrections import (
    apply_correction,
    check_confirmations,
    promote_domains,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_sender_data(
    email: str = "news@example.com",
    domain: str = "example.com",
    confidence: float = 0.9,
    override_count: int = 0,
    correct_count: int = 0,
    user_pinned: bool = False,
    last_seen: str = "2026-03-16T10:00:00Z",
    action: str = "archive",
) -> dict[str, object]:
    return {
        "email": email,
        "domain": domain,
        "display_name": "Test",
        "category": "newsletter",
        "action": action,
        "label": "mailfiler/newsletter",
        "confidence": confidence,
        "source": "heuristic",
        "has_list_unsub": True,
        "has_precedence": "bulk",
        "dkim_valid": True,
        "spf_pass": True,
        "esp_fingerprint": None,
        "seen_count": 5,
        "correct_count": correct_count,
        "override_count": override_count,
        "last_seen": last_seen,
        "first_seen": "2026-01-01T10:00:00Z",
        "user_pinned": user_pinned,
        "notes": None,
    }


class TestApplyCorrection:
    def test_confidence_decays_on_override(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, _make_sender_data(confidence=0.9))
        apply_correction(conn, "news@example.com")
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        # 0.9 * 0.6 = 0.54
        assert abs(profile["confidence"] - 0.54) < 0.01
        assert profile["override_count"] == 1
        conn.close()

    def test_override_count_increments(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, _make_sender_data(override_count=1))
        apply_correction(conn, "news@example.com")
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert profile["override_count"] == 2
        conn.close()

    def test_three_overrides_pins_sender(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, _make_sender_data(override_count=2))
        apply_correction(conn, "news@example.com")
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert profile["override_count"] == 3
        assert profile["user_pinned"] == 1
        conn.close()

    def test_correction_for_unknown_sender_is_noop(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        # Should not raise
        apply_correction(conn, "unknown@example.com")
        conn.close()


class TestCheckConfirmations:
    def test_confirmation_boosts_confidence(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, _make_sender_data(
            confidence=0.85,
            correct_count=2,
        ))
        check_confirmations(conn, ["news@example.com"])
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert abs(profile["confidence"] - 0.90) < 0.01
        assert profile["correct_count"] == 3
        conn.close()

    def test_confidence_capped_at_1(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, _make_sender_data(confidence=0.98))
        check_confirmations(conn, ["news@example.com"])
        profile = get_sender_profile(conn, "news@example.com")
        assert profile is not None
        assert profile["confidence"] <= 1.0
        conn.close()

    def test_empty_list_is_noop(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        check_confirmations(conn, [])
        conn.close()


class TestPromoteDomains:
    def test_promotes_domain_with_3_qualifying_senders(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        for i in range(3):
            upsert_sender_profile(conn, _make_sender_data(
                email=f"user{i}@example.com",
                confidence=0.9,
                action="archive",
            ))
        promoted = promote_domains(conn)
        assert "example.com" in promoted
        conn.close()

    def test_does_not_promote_with_insufficient_senders(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        for i in range(2):
            upsert_sender_profile(conn, _make_sender_data(
                email=f"user{i}@example.com",
                confidence=0.9,
            ))
        promoted = promote_domains(conn)
        assert "example.com" not in promoted
        conn.close()

    def test_does_not_promote_with_mixed_actions(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        upsert_sender_profile(conn, _make_sender_data(
            email="a@example.com", action="archive", confidence=0.9,
        ))
        upsert_sender_profile(conn, _make_sender_data(
            email="b@example.com", action="archive", confidence=0.9,
        ))
        upsert_sender_profile(conn, _make_sender_data(
            email="c@example.com", action="keep_inbox", confidence=0.9,
        ))
        promoted = promote_domains(conn)
        assert "example.com" not in promoted
        conn.close()

    def test_does_not_promote_low_confidence(self, tmp_db_path: Path) -> None:
        conn = initialize_db(tmp_db_path)
        for i in range(3):
            upsert_sender_profile(conn, _make_sender_data(
                email=f"user{i}@example.com",
                confidence=0.7,  # below 0.85 threshold
            ))
        promoted = promote_domains(conn)
        assert "example.com" not in promoted
        conn.close()
