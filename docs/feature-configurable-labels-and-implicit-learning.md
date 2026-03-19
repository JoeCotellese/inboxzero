# Configurable Labels & Implicit Learning — Feature Brief

## Summary

**What**: Make the label taxonomy configuration-driven and add SaneBox-style implicit learning that detects user corrections in Gmail and updates sender profiles automatically.

**Why**: The hardcoded label list limits flexibility. Manual `trust`/`block` commands create friction compared to SaneBox's "just use your email" training model.

**Who**: mailfiler operator (single-user local daemon)

**Priority**: P1-High — core UX improvement that makes the system adaptive

---

## Objectives

### User Objective
Customize email categories without editing code, and have mailfiler learn preferences through normal Gmail usage instead of CLI commands.

### Business Objective
Reduce misclassification rate over time through continuous implicit feedback. Make the system self-improving.

### Success Metrics
- **Learning accuracy**: >95% of detected corrections result in correct future classifications for that sender
- **Configuration coverage**: Any label added to config.toml is available across heuristics validation + LLM classification within the same run
- **Audit visibility**: Every learned correction is visible in `mailfiler audit --learned`

---

## Feature 1: Configurable Labels

### Current State
`LABEL_SUFFIXES` is a hardcoded tuple in `models.py`. The prefix is configurable but the categories are not.

### Target State
Labels defined in `config.toml` with optional descriptions for LLM guidance:

```toml
[labels]
prefix = "mailfiler"

[[labels.categories]]
name = "inbox"
description = "Important emails that need attention — personal messages, direct requests, time-sensitive items"

[[labels.categories]]
name = "newsletter"
description = "Subscription content, digests, editorial emails from publications"

[[labels.categories]]
name = "marketing"
description = "Promotional emails, sales, product announcements from companies"

[[labels.categories]]
name = "github"
description = "GitHub notifications — PRs, issues, CI results, review requests"

[[labels.categories]]
name = "jira"
description = "Jira/Atlassian notifications — ticket updates, comments, assignments"

[[labels.categories]]
name = "automated"
description = "Machine-generated messages — monitoring alerts, cron output, CI/CD"

[[labels.categories]]
name = "receipts"
description = "Purchase confirmations, invoices, shipping notifications, payment receipts"

[[labels.categories]]
name = "calendar"
description = "Calendar invitations, event updates, scheduling requests"

[[labels.categories]]
name = "security"
description = "Password resets, 2FA codes, login alerts, security notifications"

[[labels.categories]]
name = "archived"
description = "Low-value or unclassifiable emails — catch-all for everything else"
```

### Design Decisions

1. **Backwards compatible** — If `labels.categories` is absent, fall back to the current hardcoded defaults. Existing configs keep working.

2. **LLM-only for custom labels** — The heuristics layer validates that any label it produces exists in the configured set, but only has detection logic for built-in patterns. Custom labels (e.g., `travel`) are classified by the LLM using the `description` field.

3. **Description is optional** — Built-in labels have hardcoded default descriptions. Custom labels without descriptions get a generic "Emails matching the '{name}' category" prompt.

4. **Label validation** — The `archived` label is required (catch-all). The `inbox` label is required (keep-inbox target). Config loading fails if either is missing.

### Changes Required

| File | Change |
|------|--------|
| `config.py` | Add `LabelCategory` model, update `LabelsConfig` with `categories` list, add backward-compat defaults |
| `models.py` | Remove `LABEL_SUFFIXES` constant, add helper to extract suffix list from config |
| `pipeline/llm.py` | Update `build_prompt()` to include label descriptions from config |
| `pipeline/heuristics.py` | Validate produced labels against configured set |
| `config.toml.example` | Add full `[[labels.categories]]` section with descriptions |

---

## Feature 2: Implicit Learning

### How It Works

Each `mailfiler run` cycle becomes:

```
fetch unread → learn from corrections → classify → execute
```

The **learn phase** scans recently processed emails and checks their current Gmail state. Mismatches between mailfiler's decision and the email's current location indicate a user correction.

### Detection Logic

#### User moved email back to Inbox (override → keep_inbox)
- **Signal**: Email has INBOX label but `processed_emails.action_taken` was `archive`
- **Action**: Update sender_profile to `action=keep_inbox, user_pinned=true, source=user_learned`
- **Label**: Set to `{prefix}/inbox`

#### User archived email mailfiler kept (override → archive)
- **Signal**: Email lacks INBOX label and has no `mailfiler/*` label, but `action_taken` was `keep_inbox`
- **Action**: Update sender_profile to `action=archive, source=user_learned`
- **Label**: Retain the LLM/heuristic label suggestion if available, else `{prefix}/archived`
- **Note**: `user_pinned` is NOT set for archive corrections — allows re-evaluation if user later moves a different email from same sender back to inbox

#### User changed the label (override → relabel)
- **Signal**: Email has a different `mailfiler/*` label than `processed_emails.label_applied`
- **Action**: Update sender_profile label to match the user's choice
- **Note**: This handles the case where the user drags an email from `mailfiler/newsletter` to `mailfiler/inbox` via Gmail label management

### Reconciliation Tracking

Add to `processed_emails` table:

```sql
reconciled_at   TEXT,           -- NULL until learning phase checks this email
learned_action  TEXT,           -- The correction detected (NULL if no override)
```

The learn phase only queries emails where `reconciled_at IS NULL` and `processed_at` is within the lookback window. After checking, it sets `reconciled_at` regardless of whether a correction was found.

### Lookback Window

- Default: all unreconciled emails (no time limit)
- Gmail API query: fetch message metadata for unreconciled message IDs in batches
- Rate consideration: batch Gmail API calls (100 per batch) to stay within quotas

### Pipeline Integration

```python
class LearningPhase:
    """Detect user corrections by comparing processed_emails to current Gmail state."""

    def learn(self, conn, mail_client) -> list[LearnedCorrection]:
        """Scan unreconciled emails, detect overrides, update sender profiles.

        Returns list of corrections for audit logging.
        """
```

Called from `PipelineProcessor` at the start of `process_batch()`, before classification begins.

### CLI Integration

```
mailfiler run                  # learn + classify (default)
mailfiler run --no-learn       # classify only, skip learning phase
mailfiler audit --learned      # show only learned corrections
mailfiler audit --source user_learned  # same, using existing source filter
```

### Changes Required

| File | Change |
|------|--------|
| `db/schema.py` | Add `reconciled_at`, `learned_action` columns to `processed_emails` |
| `db/queries.py` | Add `get_unreconciled_emails()`, `mark_reconciled()`, `record_learned_correction()` |
| `pipeline/learning.py` | New module — `LearningPhase` class with detection logic |
| `pipeline/processor.py` | Call `LearningPhase.learn()` at start of batch processing |
| `cli.py` | Add `--no-learn` flag to `run`, add `--learned` filter to `audit` |
| `models.py` | Add `DecisionSource.USER_LEARNED = "user_learned"` |
| `mail/protocol.py` | Add `get_message_labels(message_id)` to `MailClient` protocol |
| `mail/gmail_client.py` | Implement `get_message_labels()` — lightweight metadata fetch |

---

## Audit Visibility

The `audit` command gains a `Learned` indicator:

```
$ mailfiler audit --learned

Last 3 learned corrections
Time                Action       Source          Conf  From                          Subject                     Label               Learned
2026-03-18 09:15   keep_inbox   user_learned    1.00  alice@example.com             Q1 Planning                 mailfiler/inbox     archive → keep_inbox
2026-03-18 09:15   archive      user_learned    0.90  deals@store.com               50% Off Sale                mailfiler/marketing  keep_inbox → archive
2026-03-18 09:15   archive      user_learned    0.90  news@tech.io                  Weekly Digest               mailfiler/newsletter mailfiler/marketing → mailfiler/newsletter
```

The `Learned` column shows the correction: `{old_action} → {new_action}` or `{old_label} → {new_label}`.

During normal `mailfiler run`, learned corrections are logged to stdout:

```
Learned: alice@example.com → keep_inbox (was: archive)
Learned: deals@store.com → archive/marketing (was: keep_inbox)
```

---

## Config Example (Complete)

```toml
[labels]
prefix = "mailfiler"

[[labels.categories]]
name = "inbox"
description = "Important emails that need attention — personal messages, direct requests, time-sensitive items"

[[labels.categories]]
name = "newsletter"
description = "Subscription content, digests, editorial emails from publications"

[[labels.categories]]
name = "marketing"
description = "Promotional emails, sales, product announcements from companies"

[[labels.categories]]
name = "github"
description = "GitHub notifications — PRs, issues, CI results, review requests"

[[labels.categories]]
name = "jira"
description = "Jira/Atlassian notifications — ticket updates, comments, assignments"

[[labels.categories]]
name = "automated"
description = "Machine-generated messages — monitoring alerts, cron output, CI/CD"

[[labels.categories]]
name = "receipts"
description = "Purchase confirmations, invoices, shipping notifications, payment receipts"

[[labels.categories]]
name = "calendar"
description = "Calendar invitations, event updates, scheduling requests"

[[labels.categories]]
name = "security"
description = "Password resets, 2FA codes, login alerts, security notifications"

[[labels.categories]]
name = "archived"
description = "Low-value or unclassifiable emails — catch-all for everything else"

# Custom labels — classified by LLM using the description
[[labels.categories]]
name = "travel"
description = "Flight confirmations, hotel bookings, rental cars, itineraries, travel alerts"

[[labels.categories]]
name = "finance"
description = "Bank statements, investment updates, tax documents, financial alerts"
```

---

## Implementation Order

### Phase 1: Configurable Labels
1. Update `config.py` with `LabelCategory` model and backward-compat defaults
2. Remove `LABEL_SUFFIXES` from `models.py`, add config-driven helper
3. Update `build_prompt()` in `llm.py` to use descriptions
4. Validate heuristic labels against configured set
5. Update `config.toml.example`
6. Tests: config loading, backward compat, LLM prompt generation, heuristic validation

### Phase 2: Implicit Learning
1. Schema migration: add columns to `processed_emails`
2. New queries: unreconciled emails, mark reconciled, record correction
3. Gmail client: `get_message_labels()` method
4. `LearningPhase` class with detection logic
5. Wire into `PipelineProcessor`
6. CLI: `--no-learn` flag, `--learned` audit filter
7. Tests: detection logic for all three correction types, reconciliation, audit display

### Phase 3: Polish
1. Rich CLI output for learned corrections during run
2. Stats in audit summary (e.g., "12 corrections learned this week")

---

## Risks

| Risk | Mitigation |
|------|------------|
| Gmail API rate limits from checking message state | Batch label checks (100 per API call), only check unreconciled |
| False positive corrections (email moved by filter, not user) | Only learn from emails in mailfiler-managed labels; ignore external filter moves |
| Label removed from config but still in sender_profiles | Validate sender_profile labels against config on cache lookup; log warning if stale |
| TOML array-of-tables syntax unfamiliar to users | Clear examples in config.toml.example, validation errors guide user |

---

## Done When

- [ ] Labels are fully driven by `config.toml` with backward compatibility
- [ ] Custom labels with descriptions are classified by LLM
- [ ] Learning phase detects inbox→archive and archive→inbox corrections
- [ ] Learning phase detects label changes
- [ ] Sender profiles updated automatically from corrections
- [ ] `mailfiler audit --learned` shows correction history
- [ ] `mailfiler run --no-learn` disables learning
- [ ] Run output shows learned corrections
- [ ] All three test types pass (unit, integration, e2e)
