"""Click-based CLI entry point for mailfiler."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from mailfiler.config import load_config
from mailfiler.db.queries import (
    delete_processed_email,
    delete_sender_profile,
    get_processed_by_label,
    get_sender_profile,
    list_processed_emails,
    upsert_processed_email,
    upsert_sender_profile,
)
from mailfiler.db.schema import initialize_db
from mailfiler.models import Action

if TYPE_CHECKING:
    import sqlite3
    from typing import Any

    from mailfiler.config import AppConfig
    from mailfiler.mail.gmail_client import GmailMailClient
    from mailfiler.pipeline.processor import PipelineProcessor, ProcessResult

CACHE_STALE_HOURS = 24

console = Console(width=120)

# Action → display style mapping
_ACTION_STYLES = {
    "archive": "yellow",
    "label": "cyan",
    "keep_inbox": "green",
    "mark_read": "dim",
    "trash": "red",
}

# Decision source → display style mapping
_SOURCE_STYLES = {
    "cache:sender": "blue",
    "cache:domain": "blue",
    "heuristic": "magenta",
    "llm": "bright_yellow",
    "user_learned": "green",
}


@click.group()
@click.option(
    "--config", "config_path",
    default="config.toml",
    type=click.Path(exists=True),
    help="Path to config.toml",
)
@click.pass_context
def cli(ctx: click.Context, config_path: str) -> None:
    """mailfiler — Local Gmail triage daemon."""
    ctx.ensure_object(dict)
    config = load_config(Path(config_path))
    ctx.obj["config"] = config
    ctx.obj["conn"] = initialize_db(Path(config.database.path).expanduser())


@cli.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start the polling daemon in the background."""
    config: AppConfig = ctx.obj["config"]
    pid_path = Path(config.daemon.pid_file).expanduser()

    from mailfiler.daemon import PIDFile

    pid_file = PIDFile(pid_path)
    if pid_file.read() is not None:
        console.print("[red]Daemon appears to already be running.[/] Use 'mailfiler stop' first.")
        return

    console.print(
        f"Daemon start is a placeholder — use [bold]mailfiler run[/] for foreground mode.\n"
        f"Run mode: [cyan]{config.daemon.run_mode}[/]"
    )


@cli.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Stop the running daemon."""
    config: AppConfig = ctx.obj["config"]
    pid_path = Path(config.daemon.pid_file).expanduser()

    from mailfiler.daemon import stop_daemon

    stopped = stop_daemon(pid_path)
    if stopped:
        console.print("[green]Daemon stopped.[/]")
    else:
        console.print("[dim]Daemon is not running.[/]")


def _build_pipeline(
    config: AppConfig, conn: sqlite3.Connection
) -> tuple[PipelineProcessor, GmailMailClient]:
    """Set up Gmail client, LLM provider, and pipeline processor.

    Shared by the ``run`` and ``reprocess`` commands.
    """
    from mailfiler.mail.gmail_auth import get_gmail_service
    from mailfiler.mail.gmail_client import GmailMailClient as _GmailMailClient
    from mailfiler.pipeline.cache import CacheLayer
    from mailfiler.pipeline.heuristics import HeuristicsLayer
    from mailfiler.pipeline.llm import (
        AnthropicLLMProvider,
        LLMLayer,
        LMStudioLLMProvider,
        StubLLMProvider,
    )

    creds_path = Path(config.gmail.credentials_file).expanduser()
    token_path = Path(config.gmail.token_file).expanduser()

    from mailfiler.pipeline.processor import (
        PipelineProcessor as _PipelineProcessor,
    )

    service = get_gmail_service(creds_path, token_path)
    mail_client = _GmailMailClient(service, labels_prefix=config.labels.prefix)

    # Select LLM provider based on config
    # When model is unset, each provider falls back to its own default
    model_kwargs: dict[str, str] = {}
    if config.llm.model:
        model_kwargs["model"] = config.llm.model

    label_categories = config.labels.get_categories()

    if config.llm.provider == "lmstudio":
        llm_provider = LMStudioLLMProvider(
            **model_kwargs,
            base_url=config.llm.base_url or "http://localhost:1234/v1",
            max_tokens=config.llm.max_tokens,
            timeout_seconds=config.llm.timeout_seconds,
            labels_prefix=config.labels.prefix,
            label_categories=label_categories,
        )
    elif config.llm.provider == "anthropic":
        llm_provider = AnthropicLLMProvider(
            **model_kwargs,
            max_tokens=config.llm.max_tokens,
            timeout_seconds=config.llm.timeout_seconds,
            labels_prefix=config.labels.prefix,
            label_categories=label_categories,
        )
    else:
        console.print(
            f"[yellow]Unknown LLM provider '{config.llm.provider}', "
            f"using stub (keep_inbox)[/]"
        )
        llm_provider = StubLLMProvider()

    # Preflight: check LLM provider connectivity
    healthy, health_msg = llm_provider.check_health()
    if not healthy:
        console.print(f"[yellow]LLM provider unavailable: {health_msg}[/]")
        console.print(
            "[yellow]Falling back to stub (ambiguous emails → keep_inbox)[/]"
        )
        console.print()
        llm_provider = StubLLMProvider()

    processor = _PipelineProcessor(
        mail_client=mail_client,
        cache_layer=CacheLayer(),
        heuristics_layer=HeuristicsLayer(),
        llm_layer=LLMLayer(
            provider=llm_provider,
            llm_threshold=config.rules.llm_threshold,
        ),
        conn=conn,
        run_mode=config.daemon.run_mode,
        config=config,
    )
    return processor, mail_client


@cli.command()
@click.option("--no-learn", is_flag=True, default=False, help="Skip implicit learning phase")
@click.pass_context
def run(ctx: click.Context, no_learn: bool) -> None:
    """Run one processing pass in the foreground."""
    config: AppConfig = ctx.obj["config"]
    conn: sqlite3.Connection = ctx.obj["conn"]

    processor, mail_client = _build_pipeline(config, conn)

    console.print(
        f"[bold]mailfiler[/] [dim]|[/] mode: [cyan]{config.daemon.run_mode}[/] "
        f"[dim]|[/] llm: [cyan]{config.llm.provider}[/]"
    )
    console.print()

    # Implicit learning phase
    if not no_learn:
        from mailfiler.pipeline.learning import LearningPhase

        with console.status("[bold]Checking for user corrections..."):
            learning = LearningPhase()
            corrections = learning.learn(conn, mail_client, config)

        if corrections:
            console.print(f"[green]Learned {len(corrections)} correction(s):[/]")
            for c in corrections:
                console.print(
                    f"  {c.from_email}: [yellow]{c.old_action}[/] → [green]{c.new_action}[/]"
                )
            console.print()

    with console.status("[bold]Fetching unread emails..."):
        emails = mail_client.fetch_unread(max_results=config.gmail.max_emails_per_run)

    if not emails:
        console.print("[green bold]Inbox zero![/] Nothing to process.")
        return

    console.print(f"Found [bold]{len(emails)}[/] unread emails.\n")

    # Build results table live
    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Action", width=12)
    table.add_column("Source", width=14)
    table.add_column("Conf", width=5, justify="right")
    table.add_column("From", width=30, no_wrap=True, overflow="ellipsis")
    table.add_column("Subject", no_wrap=True, overflow="ellipsis")

    count = 0

    def on_result(result: ProcessResult) -> None:
        nonlocal count
        count += 1

        action_style = _ACTION_STYLES.get(result.action.value, "white")
        source_style = _SOURCE_STYLES.get(result.decision_source.value, "white")

        executed_marker = "" if result.executed else " [dim](dry)[/]"

        table.add_row(
            str(count),
            f"[{action_style}]{result.action.value}[/]{executed_marker}",
            f"[{source_style}]{result.decision_source.value}[/]",
            f"{result.confidence:.0%}",
            result.from_email[:30],
            (result.subject or "(no subject)")[:50],
        )

    processed = processor.process_batch(emails, on_result=on_result)

    if count > 0:
        console.print(table)

    console.print()
    console.print(
        f"[bold green]Done.[/] Processed [bold]{processed}[/]/{len(emails)} emails."
    )


def _apply_reclassification(
    conn: sqlite3.Connection,
    mail_client: GmailMailClient,
    gmail_id: str,
    record: Any,
    result: Any,
    new_label: str | None,
) -> None:
    """Apply a reclassification to Gmail and update DB records."""
    old_label = record["label_applied"]
    if old_label:
        mail_client.remove_label(gmail_id, old_label)
    mail_client.apply_action(gmail_id, result.action, new_label)

    upsert_processed_email(conn, {
        "gmail_message_id": gmail_id,
        "gmail_thread_id": record["gmail_thread_id"],
        "from_email": record["from_email"],
        "from_domain": record["from_domain"],
        "subject": record["subject"],
        "received_at": record["received_at"],
        "processed_at": record["processed_at"],
        "action_taken": result.action.value,
        "label_applied": new_label,
        "decision_source": result.decision_source.value,
        "confidence": result.confidence,
        "llm_category": None,
        "llm_reason": result.llm_reason,
        "was_overridden": False,
    })

    existing_profile = get_sender_profile(conn, record["from_email"])
    if existing_profile is not None:
        profile_data = dict(existing_profile)
        profile_data["action"] = result.action.value
        profile_data["label"] = new_label
        profile_data["confidence"] = result.confidence
        profile_data["source"] = result.decision_source.value
        upsert_sender_profile(conn, profile_data)


def _cache_path_for(config: AppConfig) -> Path:
    """Return the path for reprocess_pending.json next to the DB file."""
    return Path(config.database.path).expanduser().parent / "reprocess_pending.json"


def _apply_from_cache(
    config: AppConfig,
    conn: sqlite3.Connection,
    mail_client: GmailMailClient,
) -> None:
    """Apply cached dry-run results from reprocess_pending.json."""
    from mailfiler.models import Action as _Action, DecisionSource

    cache_file = _cache_path_for(config)
    if not cache_file.exists():
        console.print("[red]No cached dry-run results found.[/] Run a dry-run first:")
        console.print("  [bold]mailfiler reprocess --label <label>[/]")
        return

    data = json.loads(cache_file.read_text())

    # Warn if cache is stale
    created = datetime.fromisoformat(data["created_at"])
    age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
    if age_hours > CACHE_STALE_HOURS:
        console.print(
            f"[yellow]Cache is {age_hours:.0f}h old (stale). Proceeding anyway.[/]"
        )

    changes = data.get("changes", [])
    deleted = data.get("deleted", [])
    summary = data.get("summary", {})

    console.print(
        f"[bold]mailfiler reprocess[/] [green]APPLY FROM CACHE[/] "
        f"[dim]|[/] labels: [cyan]{', '.join(data.get('labels', []))}[/]"
    )
    console.print(
        f"  {summary.get('changed', len(changes))} changes, "
        f"{summary.get('deleted', len(deleted))} deletions"
    )
    console.print()

    # Build a fake ProcessResult-like for _apply_reclassification
    from dataclasses import dataclass

    @dataclass
    class _CachedResult:
        action: _Action
        label: str | None
        confidence: float
        decision_source: DecisionSource
        llm_reason: str | None

    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("From", width=30, no_wrap=True, overflow="ellipsis")
    table.add_column("Subject", no_wrap=True, overflow="ellipsis")
    table.add_column("Old Label", width=20, no_wrap=True, overflow="ellipsis")
    table.add_column("New Label", width=20, no_wrap=True, overflow="ellipsis")
    table.add_column("Status", width=12)

    applied_count = 0
    for change in changes:
        gmail_id = change["gmail_message_id"]

        # Build a row-like dict matching what _apply_reclassification expects
        record = {
            "gmail_message_id": gmail_id,
            "gmail_thread_id": change["gmail_thread_id"],
            "from_email": change["from_email"],
            "from_domain": change["from_domain"],
            "subject": change["subject"],
            "received_at": change["received_at"],
            "processed_at": change["processed_at"],
            "label_applied": change["old_label"],
        }
        result = _CachedResult(
            action=_Action(change["new_action"]),
            label=change["new_label"],
            confidence=change["new_confidence"],
            decision_source=DecisionSource(change["new_decision_source"]),
            llm_reason=change.get("new_llm_reason"),
        )

        _apply_reclassification(
            conn, mail_client, gmail_id, record, result, change["new_label"],
        )
        applied_count += 1
        table.add_row(
            change["from_email"][:30],
            (change["subject"] or "(no subject)")[:50],
            change["old_label"] or "",
            f"[green]{change['new_label']}[/]",
            "[green]applied[/]",
        )

    for gmail_id in deleted:
        delete_processed_email(conn, gmail_id)

    if applied_count > 0 or deleted:
        console.print(table)

    console.print()
    console.print(
        f"[bold]Summary:[/] "
        f"[green]{applied_count} applied[/], "
        f"[red]{len(deleted)} deleted[/]"
    )

    cache_file.unlink()
    console.print(f"[dim]Cache file removed.[/]")


@cli.command()
@click.option("--label", multiple=True, help="Label to reprocess (repeatable)")
@click.option(
    "--apply", "apply_changes", is_flag=True, default=False,
    help="Apply changes to Gmail",
)
@click.option("--limit", default=100, help="Max emails per label")
@click.pass_context
def reprocess(ctx: click.Context, label: tuple[str, ...], apply_changes: bool, limit: int) -> None:
    """Re-classify emails under specific labels against current rules."""
    config: AppConfig = ctx.obj["config"]
    conn: sqlite3.Connection = ctx.obj["conn"]

    # --apply without --label: apply from cache
    if apply_changes and not label:
        processor, mail_client = _build_pipeline(config, conn)
        _apply_from_cache(config, conn, mail_client)
        return

    # Neither --apply nor --label: show usage hint
    if not label:
        console.print("[red]Specify at least one --label to scan, or use --apply to apply cached results.[/]")
        raise SystemExit(1)

    processor, mail_client = _build_pipeline(config, conn)

    mode_label = "[green]APPLY[/]" if apply_changes else "[yellow]DRY RUN[/]"
    console.print(
        f"[bold]mailfiler reprocess[/] {mode_label} "
        f"[dim]|[/] labels: [cyan]{', '.join(label)}[/]"
    )
    console.print()

    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("From", width=30, no_wrap=True, overflow="ellipsis")
    table.add_column("Subject", no_wrap=True, overflow="ellipsis")
    table.add_column("Old Label", width=20, no_wrap=True, overflow="ellipsis")
    table.add_column("New Label", width=20, no_wrap=True, overflow="ellipsis")
    table.add_column("Status", width=12)

    total = 0
    changed = 0
    deleted = 0
    unchanged = 0
    prefix = config.labels.prefix + "/"

    pending_changes: list[dict[str, object]] = []
    pending_deleted: list[str] = []

    for lbl in label:
        records = get_processed_by_label(conn, lbl, limit=limit)
        if not records:
            console.print(f"[dim]No emails found under {lbl}[/]")
            continue

        console.print(
            f"[bold]{lbl}[/]: {len(records)} emails — fetching from Gmail...",
            end="",
        )
        gmail_ids = [r["gmail_message_id"] for r in records]
        fetched = mail_client.fetch_messages(gmail_ids)
        console.print(
            f" [green]{len(fetched)}[/] found, "
            f"[red]{len(records) - len(fetched)}[/] deleted"
        )

        for record in records:
            total += 1
            gmail_id = record["gmail_message_id"]
            email = fetched.get(gmail_id)

            if email is None:
                delete_processed_email(conn, gmail_id)
                deleted += 1
                pending_deleted.append(gmail_id)
                table.add_row(
                    record["from_email"][:30],
                    (record["subject"] or "(no subject)")[:50],
                    lbl, "", "[red]deleted[/]",
                )
                continue

            result = processor.reprocess_email(email)
            old_label = record["label_applied"]
            new_label = result.label
            if result.action is Action.KEEP_INBOX:
                new_label = f"{prefix}inbox"

            from_col = email.from_email[:30]
            subj_col = (email.subject or "(no subject)")[:50]

            if old_label == new_label:
                unchanged += 1
                table.add_row(
                    from_col, subj_col,
                    old_label or "", new_label or "",
                    "[dim]unchanged[/]",
                )
                continue

            changed += 1
            if apply_changes:
                _apply_reclassification(
                    conn, mail_client, gmail_id, record, result, new_label,
                )
                table.add_row(
                    from_col, subj_col,
                    old_label or "", f"[green]{new_label}[/]",
                    "[green]applied[/]",
                )
            else:
                pending_changes.append({
                    "gmail_message_id": gmail_id,
                    "gmail_thread_id": record["gmail_thread_id"],
                    "from_email": record["from_email"],
                    "from_domain": record["from_domain"],
                    "subject": record["subject"],
                    "received_at": record["received_at"],
                    "processed_at": record["processed_at"],
                    "old_label": old_label,
                    "new_label": new_label,
                    "new_action": result.action.value,
                    "new_decision_source": result.decision_source.value,
                    "new_confidence": result.confidence,
                    "new_llm_reason": result.llm_reason,
                })
                table.add_row(
                    from_col, subj_col,
                    old_label or "", f"[cyan]{new_label}[/]",
                    "[yellow]pending[/]",
                )

    if total > 0:
        console.print(table)

    console.print()
    console.print(
        f"[bold]Summary:[/] {total} scanned, "
        f"[green]{changed} changed[/], "
        f"[dim]{unchanged} unchanged[/], "
        f"[red]{deleted} deleted[/]"
    )

    # Save cache on dry-run
    if not apply_changes and (pending_changes or pending_deleted):
        cache_file = _cache_path_for(config)
        cache_data = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "labels": list(label),
            "changes": pending_changes,
            "deleted": pending_deleted,
            "summary": {
                "total": total,
                "changed": changed,
                "unchanged": unchanged,
                "deleted": deleted,
            },
        }
        cache_file.write_text(json.dumps(cache_data, indent=2))
        console.print(f"\nTo apply: [bold]mailfiler reprocess --apply[/]")
    elif not apply_changes and changed == 0:
        console.print(f"\n[dim]No changes to apply.[/]")


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show daemon status and recent stats."""
    config: AppConfig = ctx.obj["config"]
    pid_path = Path(config.daemon.pid_file).expanduser()

    if pid_path.exists():
        pid = pid_path.read_text().strip()
        console.print(f"[green]Daemon running[/] (PID: {pid})")
    else:
        console.print("[dim]Daemon not running[/]")

    conn: sqlite3.Connection = ctx.obj["conn"]
    recent = list_processed_emails(conn, limit=1)
    if recent:
        console.print(f"Last processed: [cyan]{recent[0]['processed_at']}[/]")
    else:
        console.print("[dim]No emails processed yet[/]")


@cli.command()
@click.option("--n", "limit", default=50, help="Number of entries to show")
@click.option("--learned", is_flag=True, default=False, help="Show only learned corrections")
@click.pass_context
def audit(ctx: click.Context, limit: int, learned: bool) -> None:
    """Show last N processed emails with decisions."""
    config: AppConfig = ctx.obj["config"]
    conn: sqlite3.Connection = ctx.obj["conn"]

    if learned:
        from mailfiler.db.queries import list_learned_corrections

        entries = list_learned_corrections(conn, limit=limit)
        if not entries:
            console.print("[dim]No learned corrections found.[/]")
            return

        table = Table(
            title=f"Last {len(entries)} learned corrections",
            show_header=True,
            header_style="bold",
            pad_edge=False,
        )
        table.add_column("Time", style="dim", width=19)
        table.add_column("From", width=30, no_wrap=True, overflow="ellipsis")
        table.add_column("Subject", no_wrap=True, overflow="ellipsis")
        table.add_column("Action", width=12)
        table.add_column("Learned", style="green", width=20)
        table.add_column("Label", style="cyan", width=20, no_wrap=True, overflow="ellipsis")

        for entry in entries:
            action = entry["action_taken"]
            learned_action = entry["learned_action"]
            action_style = _ACTION_STYLES.get(action, "white")

            table.add_row(
                entry["reconciled_at"] or "",
                entry["from_email"][:30],
                (entry["subject"] or "(no subject)")[:50],
                f"[{action_style}]{action}[/]",
                f"{action} → {learned_action}",
                entry["label_applied"] or "",
            )

        console.print(table)
        return

    entries = list_processed_emails(conn, limit=limit)

    if not entries:
        console.print("[dim]No processed emails found.[/]")
        return

    table = Table(
        title=f"Last {len(entries)} processed emails",
        show_header=True,
        header_style="bold",
        pad_edge=False,
        expand=True,
    )
    table.add_column("Action", width=10, no_wrap=True)
    table.add_column("Source", width=10, no_wrap=True)
    table.add_column("Conf", width=4, justify="right", no_wrap=True)
    table.add_column("From", width=25, no_wrap=True, overflow="ellipsis")
    table.add_column("Subject", ratio=1, overflow="fold")
    table.add_column("Label", style="cyan", width=12, no_wrap=True, overflow="ellipsis")

    prefix = config.labels.prefix + "/"

    for entry in entries:
        action = entry["action_taken"]
        source = entry["decision_source"]
        confidence = entry["confidence"]
        label = entry["label_applied"] or ""
        # Strip prefix for compact display
        if label.startswith(prefix):
            label = label[len(prefix):]

        action_style = _ACTION_STYLES.get(action, "white")
        source_style = _SOURCE_STYLES.get(source, "white")

        table.add_row(
            f"[{action_style}]{action}[/]",
            f"[{source_style}]{source}[/]",
            f"{confidence:.0%}" if confidence else "-",
            entry["from_email"][:25],
            (entry["subject"] or "(no subject)")[:60],
            label,
        )

    console.print(table)


@cli.command()
@click.argument("email")
@click.pass_context
def pin(ctx: click.Context, email: str) -> None:
    """Pin a sender — always inbox, never decays."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    profile = get_sender_profile(conn, email)
    if profile is None:
        console.print(f"[red]Sender {email} not found in database.[/]")
        return
    data = dict(profile)
    data["user_pinned"] = True
    upsert_sender_profile(conn, data)
    console.print(f"[green]Pinned[/] {email}")


@cli.command()
@click.argument("email")
@click.pass_context
def unpin(ctx: click.Context, email: str) -> None:
    """Remove pin from a sender."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    profile = get_sender_profile(conn, email)
    if profile is None:
        console.print(f"[red]Sender {email} not found in database.[/]")
        return
    data = dict(profile)
    data["user_pinned"] = False
    upsert_sender_profile(conn, data)
    console.print(f"[dim]Unpinned[/] {email}")


@cli.command()
@click.argument("email")
@click.pass_context
def trust(ctx: click.Context, email: str) -> None:
    """Set sender to keep_inbox with confidence 1.0."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    profile = get_sender_profile(conn, email)
    if profile is None:
        console.print(f"[red]Sender {email} not found in database.[/]")
        return
    data = dict(profile)
    data["action"] = "keep_inbox"
    data["confidence"] = 1.0
    data["source"] = "user_override"
    upsert_sender_profile(conn, data)
    console.print(f"[green]Trusted[/] {email} — will always keep in inbox")


@cli.command()
@click.argument("email")
@click.pass_context
def block(ctx: click.Context, email: str) -> None:
    """Set sender to archive with confidence 1.0."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    profile = get_sender_profile(conn, email)
    if profile is None:
        console.print(f"[red]Sender {email} not found in database.[/]")
        return
    data = dict(profile)
    data["action"] = "archive"
    data["confidence"] = 1.0
    data["source"] = "user_override"
    upsert_sender_profile(conn, data)
    console.print(f"[yellow]Blocked[/] {email} — will always archive")


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show accuracy stats, cache hit rate, LLM usage."""
    conn: sqlite3.Connection = ctx.obj["conn"]

    total = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
    if total == 0:
        console.print("[dim]No emails processed yet.[/]")
        return

    cache_hits = conn.execute(
        "SELECT COUNT(*) FROM processed_emails WHERE decision_source LIKE 'cache:%'"
    ).fetchone()[0]
    heuristic = conn.execute(
        "SELECT COUNT(*) FROM processed_emails WHERE decision_source = 'heuristic'"
    ).fetchone()[0]
    llm = conn.execute(
        "SELECT COUNT(*) FROM processed_emails WHERE decision_source = 'llm'"
    ).fetchone()[0]
    overridden = conn.execute(
        "SELECT COUNT(*) FROM processed_emails WHERE was_overridden = 1"
    ).fetchone()[0]

    table = Table(title="Pipeline Stats", show_header=True, header_style="bold")
    table.add_column("Metric", width=20)
    table.add_column("Count", justify="right", width=8)
    table.add_column("Pct", justify="right", width=8)

    table.add_row("Total processed", str(total), "")
    table.add_row(
        "[blue]Cache hits[/]", str(cache_hits), f"{cache_hits / total * 100:.1f}%"
    )
    table.add_row(
        "[magenta]Heuristic[/]", str(heuristic), f"{heuristic / total * 100:.1f}%"
    )
    table.add_row(
        "[bright_yellow]LLM[/]", str(llm), f"{llm / total * 100:.1f}%"
    )
    table.add_row(
        "[red]Overridden[/]", str(overridden), f"{overridden / total * 100:.1f}%"
    )

    console.print(table)

    sender_count = conn.execute("SELECT COUNT(*) FROM sender_profiles").fetchone()[0]
    domain_count = conn.execute("SELECT COUNT(*) FROM domain_profiles").fetchone()[0]
    console.print(
        f"\nKnown senders: [bold]{sender_count}[/]  |  Known domains: [bold]{domain_count}[/]"
    )


@cli.command("reset-sender")
@click.argument("email")
@click.pass_context
def reset_sender(ctx: click.Context, email: str) -> None:
    """Delete sender profile — re-evaluate from scratch."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    deleted = delete_sender_profile(conn, email)
    if deleted:
        console.print(f"[green]Reset[/] {email} — will be re-evaluated")
    else:
        console.print(f"[red]Sender {email} not found in database.[/]")
