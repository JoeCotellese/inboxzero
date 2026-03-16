# Product Requirements Document: Gmail Inbox Filing Filter

**Project:** `mailfiler` — Local Gmail Triage Daemon
**Version:** 1.0
**Status:** Draft
**Owner:** Joe Cotellese

---

## 1. Overview

`mailfiler` is a locally-running Python daemon that connects to Gmail via the Gmail API and automatically triages incoming email using a three-layer decision pipeline: a sender/domain cache lookup, a header-based heuristics engine, and an LLM classifier for ambiguous cases. The goal is sustained inbox zero with minimal manual intervention.

The system is inspired by SaneBox's sender-learning model but runs entirely under user control, with full auditability and a feedback loop that improves accuracy over time.

---

## 2. Goals

- Automatically file, label, and archive email without user intervention for high-confidence cases
- Learn sender behavior over time so repeat senders skip heuristics entirely
- Use an LLM only for genuinely ambiguous emails (target: <30% of volume)
- Provide a correction mechanism so user actions teach the system
- Run as a daemon on macOS or a Linux home lab (Docker-compatible)
- Never permanently delete email without explicit user configuration

---

## 3. Non-Goals

- No web UI in v1 (CLI + SQLite audit log only)
- No support for non-Gmail accounts in v1
- No email composition or reply sending
- No attachment processing or content analysis beyond headers and body snippet

---

## 4. Tech Stack

| Component | Choice |
|---|---|
| Language | Python 3.11+ |
| Gmail access | `google-api-python-client`, `google-auth-oauthlib` |
| Database | SQLite via `sqlite3` (stdlib) |
| LLM (default) | Anthropic Claude API — `claude-haiku-4-5` |
| LLM (optional local) | Ollama with `mistral` or `llama3` |
| Scheduling | `APScheduler` (poll every 5 minutes) |
| Config | TOML (`config.toml`) |
| Packaging | Single repo, `pip install -e .`, optional `Dockerfile` |

---

## 5. Architecture

```
Gmail API (OAuth2)
       │
       ▼
  Fetch unread emails (batch, max 50/run)
       │
       ▼
┌─────────────────────┐
│  Layer 1: Cache     │  Sender/domain DB lookup
│  (SQLite)           │  → hit + confidence ≥ 0.85 → apply action immediately
└──────────┬──────────┘
           │ cache miss or low confidence
           ▼
┌─────────────────────┐
│  Layer 2: Headers   │  Parse email headers → confidence score
│  Heuristics         │  → score ≥ 0.85 → apply action + update cache
└──────────┬──────────┘
           │ score 0.25–0.85 (ambiguous)
           ▼
┌─────────────────────┐
│  Layer 3: LLM       │  Send headers + snippet to LLM
│  Classifier         │  → structured JSON response → apply action + update cache
└──────────┬──────────┘
           │ all paths
           ▼
  Apply Gmail action (label, archive, inbox)
  Log to audit table
```

---

## 6. Gmail API Integration

### 6.1 OAuth2 Setup

- Use OAuth2 with a local credentials file (`credentials.json` from Google Cloud Console)
- Token stored in `~/.mailfiler/token.json`
- Required scopes:
  - `https://www.googleapis.com/auth/gmail.modify`
  - `https://www.googleapis.com/auth/gmail.labels`

### 6.2 Email Fetching

- Poll for unread emails in INBOX on a configurable interval (default: 5 minutes)
- Fetch full message headers + body snippet (first 500 characters)
- Batch up to 50 emails per run
- Skip emails already present in the `processed_emails` table

### 6.3 Gmail Actions

The system must support the following actions via the Gmail API:

| Action | Gmail API Operation |
|---|---|
| `archive` | Remove `INBOX` label |
| `label` | Add specified label (create if not exists) |
| `keep_inbox` | No-op (log only) |
| `mark_read` | Remove `UNREAD` label |
| `trash` | Add `TRASH` label (only if `allow_trash = true` in config) |

All actions must be applied atomically. Log every action to the audit table before executing.

---

## 7. Database Schema

All data stored in a single SQLite file at `~/.mailfiler/mailfiler.db`.

### 7.1 `sender_profiles`

```sql
CREATE TABLE sender_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT UNIQUE NOT NULL,
    domain          TEXT NOT NULL,
    display_name    TEXT,

    -- Classification
    category        TEXT NOT NULL,  -- newsletter|transactional|person|notification|vip|unknown
    action          TEXT NOT NULL,  -- archive|label|keep_inbox|trash
    label           TEXT,           -- Gmail label name or NULL
    confidence      REAL NOT NULL DEFAULT 0.5,
    source          TEXT NOT NULL,  -- heuristic|llm|user_override|promoted

    -- Header signals captured on first-seen
    has_list_unsub  INTEGER DEFAULT 0,  -- BOOLEAN
    has_precedence  TEXT,               -- bulk|list|NULL
    dkim_valid      INTEGER DEFAULT 0,  -- BOOLEAN
    spf_pass        INTEGER DEFAULT 0,  -- BOOLEAN
    esp_fingerprint TEXT,               -- mailchimp|sendgrid|klaviyo|etc

    -- Learning metadata
    seen_count      INTEGER DEFAULT 1,
    correct_count   INTEGER DEFAULT 0,
    override_count  INTEGER DEFAULT 0,
    last_seen       TEXT NOT NULL,      -- ISO8601
    first_seen      TEXT NOT NULL,      -- ISO8601

    -- User control
    user_pinned     INTEGER DEFAULT 0,  -- BOOLEAN, never decays
    notes           TEXT
);

CREATE INDEX idx_sender_email ON sender_profiles(email);
CREATE INDEX idx_sender_domain ON sender_profiles(domain);
```

### 7.2 `domain_profiles`

```sql
CREATE TABLE domain_profiles (
    domain          TEXT PRIMARY KEY NOT NULL,
    category        TEXT NOT NULL,
    action          TEXT NOT NULL,
    label           TEXT,
    confidence      REAL NOT NULL DEFAULT 0.5,
    source          TEXT NOT NULL,  -- heuristic|llm|promoted|user_override
    seen_count      INTEGER DEFAULT 1,
    sender_count    INTEGER DEFAULT 1,  -- distinct senders from this domain
    last_seen       TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    user_pinned     INTEGER DEFAULT 0
);
```

### 7.3 `processed_emails`

```sql
CREATE TABLE processed_emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id TEXT UNIQUE NOT NULL,
    gmail_thread_id TEXT,
    from_email      TEXT NOT NULL,
    from_domain     TEXT NOT NULL,
    subject         TEXT,
    received_at     TEXT,           -- ISO8601 from email headers
    processed_at    TEXT NOT NULL,  -- ISO8601 local time
    action_taken    TEXT NOT NULL,
    label_applied   TEXT,
    decision_source TEXT NOT NULL,  -- cache:sender|cache:domain|heuristic|llm
    confidence      REAL,
    llm_category    TEXT,
    llm_reason      TEXT,
    was_overridden  INTEGER DEFAULT 0  -- BOOLEAN, set when user corrects
);

CREATE INDEX idx_processed_gmail_id ON processed_emails(gmail_message_id);
CREATE INDEX idx_processed_from ON processed_emails(from_email);
CREATE INDEX idx_processed_at ON processed_emails(processed_at);
```

---

## 8. Layer 1: Sender/Domain Cache

### 8.1 Lookup Logic

On each incoming email, check in this order:

1. Exact `from_email` match in `sender_profiles` WHERE `confidence >= 0.85`
2. `from_domain` match in `domain_profiles` WHERE `confidence >= 0.85`
3. Fall through to Layer 2 if no confident match

User-pinned profiles (`user_pinned = 1`) always match regardless of confidence score.

### 8.2 Confidence Decay

Apply time-based decay to non-pinned profiles:

```
effective_confidence = stored_confidence * (0.98 ^ max(0, days_since_last_seen - 90))
```

Profiles not seen in 90+ days decay gradually. Profiles not seen in 180+ days are flagged for re-evaluation (confidence drops below 0.85 threshold and falls through to heuristics).

### 8.3 Domain Promotion

When 3 or more distinct senders from the same domain have been classified with the same action at confidence ≥ 0.85, automatically create or update a `domain_profiles` entry:

- `source = "promoted"`
- `confidence = average of contributing sender confidences`
- `sender_count = number of contributing senders`

---

## 9. Layer 2: Header Heuristics

### 9.1 Scored Header Rules

Each rule contributes a positive or negative delta to a baseline score of `0.5`. Score is clamped to `[0.0, 1.0]`.

#### Archive Signals (negative delta)

| Header / Condition | Delta |
|---|---|
| `List-Unsubscribe` present | -0.30 |
| `Precedence: bulk` or `Precedence: list` | -0.25 |
| `Auto-Submitted: auto-generated` | -0.35 |
| `Auto-Submitted: auto-replied` | -0.35 |
| `Return-Path: <>` (null return path) | -0.30 |
| `X-Mailer` matches known ESP (MailChimp, Klaviyo, HubSpot, Marketo, Constant Contact) | -0.25 |
| `X-Campaign-ID` present | -0.20 |
| `X-Mailgun-*` or `X-SendGrid-*` present | -0.15 |
| `X-Auto-Response-Suppress` present | -0.30 |
| Recipient address is CC, not TO | -0.20 |
| `From` matches `noreply@`, `no-reply@`, `donotreply@` | -0.35 |

#### Inbox / Trust Signals (positive delta)

| Header / Condition | Delta |
|---|---|
| `To` contains user's primary email directly | +0.30 |
| `From` domain in `vip_domains` config list | +0.40 |
| `From` address in `vip_senders` config list | +0.50 |
| `DKIM-Signature` present and valid | +0.10 |
| `Received-SPF: pass` | +0.10 |
| Reply-To matches From | +0.05 |

#### Override Rules (bypass scoring entirely)

| Condition | Forced Action |
|---|---|
| `X-PagerDuty-*` present | Force `keep_inbox`, confidence = 1.0 |
| `X-GitHub-*` present | Force `label:github`, confidence = 0.95 |
| `X-JIRA-*` present | Force `label:jira`, confidence = 0.95 |
| `X-Slack-*` present | Force `archive`, confidence = 0.95 |
| `From` in `blocked_senders` config | Force `trash` (if enabled), confidence = 1.0 |

### 9.2 Category Mapping

After scoring, map the final score to a category:

| Score | Category | Action |
|---|---|---|
| ≥ 0.85 | High confidence archive | `archive` + appropriate label |
| ≤ 0.25 | High confidence inbox | `keep_inbox` |
| 0.25–0.85 | Ambiguous | Pass to Layer 3 (LLM) |

### 9.3 Label Assignment from Headers

When archiving via heuristics, assign a label based on detected signals:

| Signal | Label |
|---|---|
| `List-Unsubscribe` or `Precedence: list` | `mailfiler/newsletter` |
| ESP fingerprint detected | `mailfiler/marketing` |
| `X-Stripe-*`, `X-PayPal-*`, subject contains "receipt" or "invoice" | `mailfiler/receipts` |
| `X-GitHub-*` | `mailfiler/github` |
| `X-JIRA-*` | `mailfiler/jira` |
| `Auto-Submitted` | `mailfiler/automated` |
| Default archive (no specific signal) | `mailfiler/archived` |

---

## 10. Layer 3: LLM Classifier

### 10.1 Provider Configuration

Support two providers via `config.toml`:

```toml
[llm]
provider = "anthropic"          # or "ollama"
model = "claude-haiku-4-5"      # or "mistral" for ollama
max_tokens = 500
timeout_seconds = 10
```

### 10.2 Input Construction

Send the following to the LLM (never send full body — snippet only):

```
System: You are an email triage assistant. Classify the email and return a JSON object only.
No preamble, no markdown, no explanation outside the JSON.

User:
Classify this email for inbox triage.

From: {display_name} <{from_email}>
To: {to_field}
Subject: {subject}
Date: {date}
Key Headers: {filtered_headers}
Body snippet (first 500 chars): {snippet}

Respond with this exact JSON structure:
{
  "category": "action_required|reply_needed|fyi|newsletter|receipt|notification|spam",
  "priority": "high|medium|low",
  "action": "keep_inbox|archive|label",
  "label": "<label name or null>",
  "confidence": <0.0 to 1.0>,
  "reason": "<one sentence max>"
}
```

`filtered_headers` should include only: `List-Unsubscribe`, `Precedence`, `Auto-Submitted`, `Return-Path`, `Reply-To`, `X-Mailer`, `DKIM-Signature` presence (boolean), `Received-SPF` result.

### 10.3 Response Handling

- Parse JSON response strictly
- If JSON is malformed or fields are missing, log the error and default to `keep_inbox` with `confidence = 0.0`
- If LLM call fails (timeout, API error), default to `keep_inbox` and log the failure
- Apply the returned action only if `confidence >= 0.6`; otherwise default to `keep_inbox`

### 10.4 Cache Update After LLM

After a successful LLM classification with `confidence >= 0.7`, upsert a `sender_profiles` record with `source = "llm"`. This means the next email from the same sender hits Layer 1 and never reaches the LLM.

---

## 11. Learning & Feedback Loop

### 11.1 Correction Detection

Poll for corrections every 30 minutes by checking for emails that have been moved:

- Emails the system archived that are now back in INBOX → sender confidence decayed
- Emails the system kept in inbox that are now archived → sender confidence boosted

```python
# Correction detection query
gmail.search("label:mailfiler/archived is:inbox")  # user rescued
gmail.search("-label:mailfiler is:inbox newer_than:1d")  # never touched, still inbox
```

### 11.2 Confidence Adjustment on Override

When a correction is detected:

- `override_count += 1`
- `confidence *= 0.6` (significant decay)
- If `override_count >= 3`, set `user_pinned = True` and route all future emails from this sender to LLM indefinitely (skip cache lookup)
- Log the correction in `processed_emails.was_overridden = 1`

### 11.3 Confidence Boost on Confirmation

When a user does NOT move a filed email within 7 days, treat it as implicit confirmation:
- `correct_count += 1`
- `confidence = min(1.0, confidence + 0.05)`

---

## 12. Configuration (`config.toml`)

```toml
[gmail]
credentials_file = "~/.mailfiler/credentials.json"
token_file = "~/.mailfiler/token.json"
poll_interval_minutes = 5
max_emails_per_run = 50

[llm]
provider = "anthropic"
model = "claude-haiku-4-5"
max_tokens = 500
timeout_seconds = 10

[rules]
allow_trash = false             # Never trash without explicit opt-in
confidence_threshold = 0.85    # Minimum to act without LLM
llm_threshold = 0.6             # Minimum LLM confidence to act
confirmation_days = 7           # Days before treating non-move as confirmation

[vip_senders]
# Always keep in inbox, never decay
emails = [
  "trish@example.com",
]

[vip_domains]
# Treat all senders from these domains as high-priority
domains = [
  "techstars.com",
  "comcast.com",
  "wavely.com",
]

[blocked_senders]
emails = []  # Requires allow_trash = true to take effect

[labels]
prefix = "mailfiler"            # All created labels prefixed: mailfiler/newsletter, etc.
```

---

## 13. CLI Interface

The system must expose a CLI via `mailfiler` command:

```
mailfiler start              # Start the polling daemon
mailfiler stop               # Stop the daemon
mailfiler run                # Run one processing pass immediately (foreground)
mailfiler status             # Show daemon status + stats from last 24h
mailfiler audit [--n 50]     # Show last N processed emails with decisions
mailfiler pin <email>        # Pin a sender (always inbox, never decays)
mailfiler unpin <email>      # Remove pin
mailfiler trust <email>      # Set sender to keep_inbox with confidence 1.0
mailfiler block <email>      # Set sender to archive/trash with confidence 1.0
mailfiler stats              # Show accuracy stats, cache hit rate, LLM usage
mailfiler reset-sender <email>  # Delete sender profile, re-evaluate from scratch
```

---

## 14. Audit Log Output (`mailfiler audit`)

```
2025-03-16 09:14:22  archive   [cache:sender]    news@techstars.com         "Techstars Weekly Digest"
2025-03-16 09:14:23  keep_inbox [cache:domain]   trish@example.com          "Dinner tonight?"
2025-03-16 09:14:24  archive   [heuristic:0.92]  noreply@github.com         "[mailfiler] PR #42 merged"
2025-03-16 09:14:25  label     [llm:0.78]        vendor@newco.io            "Partnership opportunity"
                                                  → mailfiler/review  reason: "Cold outreach, low urgency"
2025-03-16 09:14:26  keep_inbox [llm:0.81]       cfo@wavely.com             "EyeGuide budget approval"
```

---

## 15. Project Structure

```
mailfiler/
├── README.md
├── pyproject.toml
├── config.toml.example
├── Dockerfile
├── mailfiler/
│   ├── __init__.py
│   ├── cli.py              # Click-based CLI entry point
│   ├── daemon.py           # APScheduler polling loop
│   ├── gmail/
│   │   ├── __init__.py
│   │   ├── auth.py         # OAuth2 flow
│   │   ├── client.py       # Gmail API wrapper
│   │   └── actions.py      # Label/archive/modify operations
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── processor.py    # Main pipeline orchestrator
│   │   ├── cache.py        # Layer 1: sender/domain cache
│   │   ├── heuristics.py   # Layer 2: header scoring
│   │   └── llm.py          # Layer 3: LLM classifier
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.py       # Table creation / migrations
│   │   └── queries.py      # All DB read/write operations
│   ├── feedback/
│   │   ├── __init__.py
│   │   └── corrections.py  # Gmail correction detection + confidence updates
│   └── config.py           # TOML config loader + validation
└── tests/
    ├── test_heuristics.py
    ├── test_cache.py
    ├── test_llm.py
    └── fixtures/
        └── sample_emails.json
```

---

## 16. Phased Rollout

### Phase 1 — Observe Only (Week 1)
- System runs, processes all emails
- Logs what it *would* do but takes no action
- Operator reviews `mailfiler audit` daily to validate decisions
- Goal: confirm heuristics and LLM are classifying correctly before acting

### Phase 2 — Heuristics Only (Week 2)
- High-confidence heuristic actions execute (archive, label)
- LLM classifications are logged but not acted upon
- Monitor `mailfiler stats` for false positive rate

### Phase 3 — Full Auto (Week 3+)
- All layers active
- LLM actions execute on emails above confidence threshold
- Feedback loop active
- Review `mailfiler audit` weekly rather than daily

---

## 17. Acceptance Criteria

| # | Criterion |
|---|---|
| AC-1 | Gmail OAuth2 authentication completes without manual token refresh for 30+ days |
| AC-2 | Cache hit rate exceeds 70% after 2 weeks of normal email volume |
| AC-3 | LLM is invoked for fewer than 30% of processed emails after week 2 |
| AC-4 | No email is permanently deleted unless `allow_trash = true` and `blocked_senders` is configured |
| AC-5 | All actions are logged to `processed_emails` before execution |
| AC-6 | A sender with 3+ overrides is automatically pinned and routes to LLM indefinitely |
| AC-7 | Domain promotion triggers correctly when 3+ senders from same domain share an action |
| AC-8 | `mailfiler audit` output shows decision source (cache/heuristic/llm) for every entry |
| AC-9 | System recovers gracefully from Gmail API rate limits and LLM timeouts |
| AC-10 | All Gmail labels are prefixed with `mailfiler/` to avoid collision with existing labels |

---

## 18. Out of Scope (Future Versions)

- Web dashboard / local UI
- Multi-account Gmail support
- Calendar event detection and auto-RSVP
- Attachment analysis or virus scanning
- Mobile push notification for high-priority emails
- Export of sender profiles for backup/restore
- Support for IMAP, Outlook, or other mail providers
