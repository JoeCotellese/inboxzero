# mailfiler

Local Gmail triage daemon that automatically files, labels, and archives email using a three-layer decision pipeline. Inspired by SaneBox but runs entirely under your control.

## How It Works

```
Gmail API (OAuth2)
       |
       v
  Fetch unread emails
       |
       v
+---------------------+
|  Layer 1: Cache     |  Sender/domain DB lookup
|  (SQLite)           |  -> hit + high confidence -> apply immediately
+----------+----------+
           | miss
           v
+---------------------+
|  Layer 2: Heuristics|  Header analysis + scoring
|                     |  -> high confidence -> apply + cache
+----------+----------+
           | ambiguous
           v
+---------------------+
|  Layer 3: LLM       |  Headers + body snippet -> structured JSON
|  Classifier         |  -> apply + cache for next time
+---------------------+
```

The cache learns from every decision. Repeat senders skip heuristics and LLM entirely after the first classification.

## Features

- **Three-layer pipeline** -- cache, heuristics, LLM -- minimizes API calls
- **LLM flexibility** -- Anthropic Claude API or local models via LM Studio
- **Observe mode** -- dry-run to see what mailfiler *would* do before enabling full auto
- **Sender management** -- pin, trust, block, or reset individual senders
- **Audit log** -- every decision stored in SQLite with full provenance
- **Feedback loop** -- user corrections update the cache for future accuracy
- **Label taxonomy** -- newsletters, notifications, receipts, calendar, security, and more
- **Docker support** -- multi-stage Dockerfile included

## Quickstart

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A Google Cloud project with the Gmail API enabled
- OAuth2 credentials (`credentials.json`) for your Gmail account

### Install

```bash
git clone https://github.com/JoeCotellese/inboxzero.git
cd inboxzero
uv sync
```

### Configure

```bash
cp config.toml.example config.toml
```

Edit `config.toml` with your settings:

- Set `user_email` to your Gmail address
- Choose an LLM provider (`anthropic`, `lmstudio`, or leave blank for a stub that keeps everything in inbox)
- Adjust confidence thresholds if desired

Place your Google OAuth2 credentials at `~/.mailfiler/credentials.json`. On first run, mailfiler will open a browser for OAuth consent and save the token.

### Run

```bash
# Observe mode (dry run, no changes to Gmail)
mailfiler run

# Check what happened
mailfiler audit

# View pipeline stats
mailfiler stats
```

Once you're comfortable with the decisions, set `run_mode = "full_auto"` in `config.toml`.

## CLI Commands

| Command | Description |
|---------|-------------|
| `mailfiler run` | Run one processing pass in the foreground |
| `mailfiler status` | Show daemon status |
| `mailfiler audit` | Show recent processed emails with decisions |
| `mailfiler stats` | Show cache hit rate, LLM usage, override stats |
| `mailfiler pin <email>` | Always keep sender in inbox |
| `mailfiler unpin <email>` | Remove inbox pin |
| `mailfiler trust <email>` | Keep in inbox with max confidence |
| `mailfiler block <email>` | Always archive sender |
| `mailfiler reset-sender <email>` | Delete sender profile, re-evaluate from scratch |

## LLM Providers

**Anthropic Claude** (cloud):
```toml
[llm]
provider = "anthropic"
model = "claude-haiku-4-5"
```
Requires `ANTHROPIC_API_KEY` environment variable.

**LM Studio** (local):
```toml
[llm]
provider = "lmstudio"
model = "qwen3-30b-a3b-2507"
base_url = "http://localhost:1234/v1"
```
No API key needed. Good for privacy-first setups or 32GB+ Macs.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run pyright
```

## License

[MIT](LICENSE)
