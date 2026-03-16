"""Layer 2: Header-based heuristics scoring.

Placeholder — will be implemented in Phase 4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mailfiler.config import AppConfig
    from mailfiler.models import EmailMessage, HeuristicResult


class HeuristicsLayer:
    """Header-based email scoring with override rules."""

    def score(self, email: EmailMessage, config: AppConfig) -> HeuristicResult:
        """Score an email based on header heuristics.

        Placeholder — will be implemented in Phase 4.
        """
        raise NotImplementedError
