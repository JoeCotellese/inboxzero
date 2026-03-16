"""Action mapping logic for Gmail API operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mailfiler.models import Action


def action_to_label_mods(
    action: Action,
    label: str | None,
    labels_prefix: str,
) -> tuple[list[str], list[str]]:
    """Map an Action to Gmail label modifications.

    Args:
        action: The triage action to apply.
        label: Optional label name to add.
        labels_prefix: Config prefix for created labels.

    Returns:
        Tuple of (add_label_ids, remove_label_ids).
    """
    from mailfiler.models import Action as _Action

    add: list[str] = []
    remove: list[str] = []

    if action == _Action.ARCHIVE:
        remove.append("INBOX")
        if label:
            add.append(label)
    elif action == _Action.LABEL:
        if label:
            add.append(label)
    elif action == _Action.KEEP_INBOX:
        pass  # no-op
    elif action == _Action.MARK_READ:
        remove.append("UNREAD")
    elif action == _Action.TRASH:
        add.append("TRASH")

    return add, remove
