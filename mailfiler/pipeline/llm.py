"""Layer 3: LLM-based email classifier.

Placeholder — will be implemented in Phase 5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mailfiler.models import EmailMessage, LLMClassification


class LLMProvider(Protocol):
    """Protocol for LLM providers (Anthropic, Ollama, etc.)."""

    def classify(self, email: EmailMessage) -> LLMClassification:
        """Classify an email using an LLM. Returns structured classification."""
        ...


class LLMLayer:
    """Orchestrates LLM classification with fallback handling."""

    def classify(self, email: EmailMessage) -> LLMClassification:
        """Classify an email via LLM with error handling.

        Placeholder — will be implemented in Phase 5.
        """
        raise NotImplementedError
