"""Layer 1: Sender/domain cache lookup.

Placeholder — will be implemented in Phase 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

    from mailfiler.models import CacheResult, EmailMessage


class CacheLayer:
    """Sender and domain cache with confidence decay and pinning."""

    def lookup(self, email: EmailMessage, conn: sqlite3.Connection) -> CacheResult | None:
        """Look up sender/domain in cache. Returns None on miss.

        Placeholder — will be implemented in Phase 3.
        """
        return None
