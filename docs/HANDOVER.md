# Handover

What you are inheriting, what is deliberately unfinished, and what will bite
you if nobody reads this.

| | |
|---|---|
| **State** | Phases P0–P9 and P11 delivered. P10 (deploy) is prepared and waiting on a server. |
| **Tests** | 559, all passing |
| **Requirements** | 57 of 62 met — see [ACCEPTANCE.md](ACCEPTANCE.md) |
| **Operations** | [RUNBOOK.md](RUNBOOK.md) |
| **Design decisions** | [../PLAN.md](../PLAN.md) |

---

## 1. Three things nobody but you can do

These are not code problems. Nothing ships past them.

### 1.1 Somebody has to press send, every day

WhatsApp sending is **manual and unautomatable**. The Meta Cloud API
integration was built and then removed at the owner's request; what replaced it
is a button that opens WhatsApp with the message already typed, from the
assistant's own account.

The consequences are operational, not technical, and no code fixes them:

* **Nothing sends on a schedule.** If nobody opens the message centre, no
  family hears anything. Put it in whoever's routine closes the day.
* **One click per student.** `flask prepare-daily-updates` can write the day's
  texts in advance; it cannot send them.
* **The app never learns whether a message arrived.** `sent` means an assistant
  opened it at a recorded time. Treat it as evidence of an attempt, not proof
  of receipt, when a parent says they were never told.
* **Messages come from whoever sent them**, not from 01559306667. If they must
  come from the centre's number, that SIM has to be the one signed in to
  WhatsApp on the machine staff use.

Going back to the API is a rewrite of `services/whatsapp.py`, not a config
flag — the provider abstraction, webhook, credentials and delivery columns were
deleted rather than left dormant. Full cost accounting in
[ACCEPTANCE.md §9.6](ACCEPTANCE.md).

### 1.2 A native Arabic review — 323 strings written by a non-native speaker

Every Arabic string in this system was written by the developer. That includes
the parent-facing daily-update text and staff UI. It is not a formality.

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask export-translations
```

That writes a CSV with English, current Arabic, and an empty corrected column.
**Do this before staff learn the wording**, or you will be changing terms people
have already memorised.

The contractual terms texts in `app/seeds/terms_v1.py` came verbatim from the
brief and were *not* written by the developer — leave those alone unless the
business changes them.

### 1.3 Postgres has never actually run this schema

Every migration has only ever executed against SQLite. The schema compiles
cleanly for the Postgres dialect and the types map as designed — both asserted
in `tests/test_deployment.py` — but *compiles* is not *ran*.

```bash
./scripts/verify-postgres.sh
```

Runs the whole migration chain up, down and up again on a throwaway database,
then the full test suite against Postgres. **Do this before creating the real
database**, while a mistake costs nothing.

The Postgres backup and restore scripts have likewise never been executed. Run
`scripts/restore-drill.sh` the day you provision, not the day you need it.

---

## 2. Open questions still unanswered

Numbering matches [PLAN.md §8](../PLAN.md). Q1–Q5 and Q7 were answered during
the build.

| | Question | Consequence today |
|---|---|---|
| **Q6** | Should the terms have an English text, or stay Arabic for everyone? | `body_en` is null, so English-language users see the Arabic text. Versioning is built — supplying the text is a data change, not code. |
| **Q8** | Can a parent request a trial themselves, or is it assistant-only? | Assistant-only. There is no public page. `TestTrialGating` asserts every booking route is staff-gated — **that test is what must change** if you open it up. |
| **Q9** | Is a teacher payout report in scope? | Not built. The 60/40 split and end-of-round timing are in the teacher terms, and the session data supports the calculation — only the screens are missing. |
| **Q10** | How are refunds and mid-round withdrawals handled? | Not modelled. Payment can be reverted to unpaid and the change is audited; there is no refund concept. |
| **Q11** | Can a teacher teach multiple subjects? | `teacher_profiles.subject` is a single value. |
| **Q12** | Teacher self check-in? | Not built — the brief itself called it a v2 option. |
| **Q13** | How long must audit and message logs be kept? | Kept indefinitely. `flask archive-audit` exports and **deletes nothing**, deliberately. |

---

## 3. Two requirements deliberately not met

**The WhatsApp Cloud API integration was removed** at the owner's request —
§1.1 above, and [ACCEPTANCE.md §9.6](ACCEPTANCE.md).

**The brief says a teacher can "view/edit own weekly calendar". Editing is not
built.**

The teacher terms in the same brief require that all schedule changes go
through the assistant with 24 hours' notice. A teacher silently rewriting their
own week would contradict the agreement they sign on first login. The calendar
page says so in words instead of offering the control.

If you want teachers to edit their own schedules, it is a small change — but
the terms text should change with it. Full reasoning in
[ACCEPTANCE.md §9.1](ACCEPTANCE.md).

---

## 4. Rules a new developer will break if they do not read them

Each of these caused a real bug during the build, and each now has a test that
fails if it recurs.

1. **Route handlers never query models directly.** Call a `services/` function
   that takes the acting user first and can only return rows they may see.
2. **404 for an out-of-scope row, 403 for a role-gated area.** On a row the ID
   *is* the secret.
3. **Course visibility is not enough for feedback.** It is private to one
   student and their parents — read it through `scoping.feedback_for` only.
4. **Never write `_(something.value)`.** Extraction is static, so a runtime
   value produces no msgid and renders in English forever. Enum text lives in
   `app/labels.py`.
5. **Never name a template directory with a leading underscore.** Babel skips
   them, so every string inside silently ships untranslated.
6. **Session dates and times are naive local wall clock; event timestamps are
   aware UTC** via `UtcDateTime`. Mixing them shifts half the year by an hour.
7. **No bare `left`/`right` in CSS.** Logical properties only, or Arabic breaks.
8. **`pybabel compile` is part of deployment.** `.mo` files are gitignored;
   skipping it renders every Arabic screen in English with nothing erroring.

---

## 5. The tests that exist to stop rot

These fail when someone adds something without making a decision. They are the
reason coverage should not decay after handover.

| Test | Fails when |
|---|---|
| `test_audit.py::TestCoverageRegistry` | a mutating route ships without being classified as audited or explicitly not |
| `test_security_hardening.py::TestFullAuthorizationMatrix` | any route serves an anonymous caller or reaches across roles |
| `test_i18n_coverage.py` | a user-facing string is missing from the Arabic catalogue, or a directory is invisible to extraction |
| `test_acceptance.py::TestTraceability` | the requirements table points at a test that no longer exists, or something new becomes unmet |
| `test_deployment.py::TestDeploymentArtifacts` | the deploy stops backing up before migrating, or starts downgrading the database |

---

## 6. Routine operations

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask preflight          # before serving traffic
FLASK_APP=wsgi.py .venv-flask/bin/flask integrity-check    # orphans and contradictions
FLASK_APP=wsgi.py .venv-flask/bin/flask audit-stats        # audit volume and age
./scripts/restore-drill.sh                                  # monthly, and after any schema change
```

`integrity-check` reports and never repairs — the right fix for a damaged
record is a judgement call about a real family's data.

---

## 7. If you only remember three things

1. **Nothing sends itself.** A person opens the message centre and presses a
   button per student, or families hear nothing.
2. **Get the Arabic reviewed before staff learn it.**
3. **Run `verify-postgres.sh` before the real database exists.**
