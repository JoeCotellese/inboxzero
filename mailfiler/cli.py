"""Click-based CLI entry point for mailfiler."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from mailfiler.config import load_config
from mailfiler.db.queries import (
    delete_sender_profile,
    get_sender_profile,
    list_processed_emails,
    upsert_sender_profile,
)
from mailfiler.db.schema import initialize_db

if TYPE_CHECKING:
    import sqlite3

    from mailfiler.config import AppConfig


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
        click.echo("Daemon appears to already be running. Use 'mailfiler stop' first.")
        return

    click.echo(
        f"Daemon start is a placeholder — use 'mailfiler run' for foreground mode.\n"
        f"Run mode: {config.daemon.run_mode}"
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
        click.echo("Daemon stopped.")
    else:
        click.echo("Daemon is not running.")


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Run one processing pass in the foreground."""
    config: AppConfig = ctx.obj["config"]
    conn: sqlite3.Connection = ctx.obj["conn"]

    from mailfiler.mail.gmail_auth import get_gmail_service
    from mailfiler.mail.gmail_client import GmailMailClient
    from mailfiler.pipeline.cache import CacheLayer
    from mailfiler.pipeline.heuristics import HeuristicsLayer
    from mailfiler.pipeline.llm import AnthropicLLMProvider, LLMLayer
    from mailfiler.pipeline.processor import PipelineProcessor

    creds_path = Path(config.gmail.credentials_file).expanduser()
    token_path = Path(config.gmail.token_file).expanduser()

    service = get_gmail_service(creds_path, token_path)
    mail_client = GmailMailClient(service, labels_prefix=config.labels.prefix)

    processor = PipelineProcessor(
        mail_client=mail_client,
        cache_layer=CacheLayer(),
        heuristics_layer=HeuristicsLayer(),
        llm_layer=LLMLayer(
            provider=AnthropicLLMProvider(
                model=config.llm.model,
                max_tokens=config.llm.max_tokens,
                timeout_seconds=config.llm.timeout_seconds,
            ),
            llm_threshold=config.rules.llm_threshold,
        ),
        conn=conn,
        run_mode=config.daemon.run_mode,
        config=config,
    )

    click.echo(f"Fetching up to {config.gmail.max_emails_per_run} unread emails...")
    emails = mail_client.fetch_unread(max_results=config.gmail.max_emails_per_run)
    click.echo(f"Found {len(emails)} unread emails.")

    if emails:
        processed = processor.process_batch(emails)
        click.echo(f"Processed {processed}/{len(emails)} emails (mode: {config.daemon.run_mode})")
    else:
        click.echo("Inbox zero! Nothing to process.")


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show daemon status and recent stats."""
    config: AppConfig = ctx.obj["config"]
    pid_path = Path(config.daemon.pid_file).expanduser()

    if pid_path.exists():
        pid = pid_path.read_text().strip()
        click.echo(f"Daemon running (PID: {pid})")
    else:
        click.echo("Daemon not running")

    conn: sqlite3.Connection = ctx.obj["conn"]
    recent = list_processed_emails(conn, limit=1)
    if recent:
        click.echo(f"Last processed: {recent[0]['processed_at']}")
    else:
        click.echo("No emails processed yet")


@cli.command()
@click.option("--n", "limit", default=50, help="Number of entries to show")
@click.pass_context
def audit(ctx: click.Context, limit: int) -> None:
    """Show last N processed emails with decisions."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    entries = list_processed_emails(conn, limit=limit)

    if not entries:
        click.echo("No processed emails found.")
        return

    for entry in entries:
        source = entry["decision_source"]
        confidence = entry["confidence"]
        source_str = f"{source}:{confidence:.2f}" if confidence else source
        click.echo(
            f"{entry['processed_at']}  {entry['action_taken']:<12} "
            f"[{source_str:<20}] {entry['from_email']:<35} "
            f'"{entry["subject"]}"'
        )
        if entry["label_applied"]:
            click.echo(f"{'':>20} → {entry['label_applied']}")
        if entry["llm_reason"]:
            click.echo(f"{'':>20}   reason: \"{entry['llm_reason']}\"")


@cli.command()
@click.argument("email")
@click.pass_context
def pin(ctx: click.Context, email: str) -> None:
    """Pin a sender — always inbox, never decays."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    profile = get_sender_profile(conn, email)
    if profile is None:
        click.echo(f"Sender {email} not found in database.")
        return
    data = dict(profile)
    data["user_pinned"] = True
    upsert_sender_profile(conn, data)
    click.echo(f"Pinned {email}")


@cli.command()
@click.argument("email")
@click.pass_context
def unpin(ctx: click.Context, email: str) -> None:
    """Remove pin from a sender."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    profile = get_sender_profile(conn, email)
    if profile is None:
        click.echo(f"Sender {email} not found in database.")
        return
    data = dict(profile)
    data["user_pinned"] = False
    upsert_sender_profile(conn, data)
    click.echo(f"Unpinned {email}")


@cli.command()
@click.argument("email")
@click.pass_context
def trust(ctx: click.Context, email: str) -> None:
    """Set sender to keep_inbox with confidence 1.0."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    profile = get_sender_profile(conn, email)
    if profile is None:
        click.echo(f"Sender {email} not found in database.")
        return
    data = dict(profile)
    data["action"] = "keep_inbox"
    data["confidence"] = 1.0
    data["source"] = "user_override"
    upsert_sender_profile(conn, data)
    click.echo(f"Trusted {email} — will always keep in inbox")


@cli.command()
@click.argument("email")
@click.pass_context
def block(ctx: click.Context, email: str) -> None:
    """Set sender to archive with confidence 1.0."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    profile = get_sender_profile(conn, email)
    if profile is None:
        click.echo(f"Sender {email} not found in database.")
        return
    data = dict(profile)
    data["action"] = "archive"
    data["confidence"] = 1.0
    data["source"] = "user_override"
    upsert_sender_profile(conn, data)
    click.echo(f"Blocked {email} — will always archive")


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show accuracy stats, cache hit rate, LLM usage."""
    conn: sqlite3.Connection = ctx.obj["conn"]

    total = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
    if total == 0:
        click.echo("No emails processed yet.")
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

    click.echo(f"Total processed:   {total}")
    click.echo(f"Cache hits:        {cache_hits} ({cache_hits / total * 100:.1f}%)")
    click.echo(f"Heuristic:         {heuristic} ({heuristic / total * 100:.1f}%)")
    click.echo(f"LLM:               {llm} ({llm / total * 100:.1f}%)")
    click.echo(f"Overridden:        {overridden} ({overridden / total * 100:.1f}%)")

    sender_count = conn.execute("SELECT COUNT(*) FROM sender_profiles").fetchone()[0]
    domain_count = conn.execute("SELECT COUNT(*) FROM domain_profiles").fetchone()[0]
    click.echo(f"Known senders:     {sender_count}")
    click.echo(f"Known domains:     {domain_count}")


@cli.command("reset-sender")
@click.argument("email")
@click.pass_context
def reset_sender(ctx: click.Context, email: str) -> None:
    """Delete sender profile — re-evaluate from scratch."""
    conn: sqlite3.Connection = ctx.obj["conn"]
    deleted = delete_sender_profile(conn, email)
    if deleted:
        click.echo(f"Reset {email} — will be re-evaluated")
    else:
        click.echo(f"Sender {email} not found in database.")
