# MCA Academy — Education Centre Management System

Flask app for a single education centre: courses, teachers, students, parents,
attendance, homework, feedback, and daily WhatsApp updates.

- **[PLAN.md](PLAN.md)** is the source of truth: architecture (§1–5), the
  phase/sprint breakdown (§6), the conventions any contributor must follow (§7),
  and the open questions still awaiting the owner's answer (§8).
- **Phases P0–P9 and P11 are delivered.** P10 (deploy) is prepared and waiting on a server.
- **[docs/HANDOVER.md](docs/HANDOVER.md)** — start here if you are inheriting this.
- **[docs/RUNBOOK.md](docs/RUNBOOK.md)** — operations: provisioning, deploy,
  rollback, restore, and the staff walkthrough.
- **[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)** — every brief requirement traced
  to the code and test that satisfies it.

## Status

| | |
|---|---|
| Tests | 559 passing |
| Lint | `ruff` clean |
| Migrations | verified up and down |
| Languages | Arabic (default, RTL) and English |

## Local setup

Python 3.12 — not 3.13/3.14, which several dependencies have no wheels for yet.

```bash
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .venv-flask
```

```bash
.venv-flask/bin/pip install -r requirements-dev.txt
```

```bash
cp .env.example .env
```

Create the database, seed the fixed data, and make the admin account:

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask db upgrade
```

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask seed-terms
```

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask seed-course-types
```

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask create-admin --name "Center Owner" --phone 01000000001
```

Once courses exist, lay out their sessions (idempotent, safe to re-run):

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask generate-sessions
```

`create-admin` prints a generated password **once**. It is not recoverable — write
it down. Sign in and you will be forced to change it before reaching any page.

Run it:

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask run --debug --port 5001
```

## Tests and lint

```bash
.venv-flask/bin/python -m pytest -q
```

```bash
.venv-flask/bin/ruff check app tests wsgi.py
```

## Translations

Every user-facing string goes through `_()` / `_l()`. After adding or changing one:

```bash
.venv-flask/bin/pybabel extract -F babel.cfg -k _l -k _ -o messages.pot . && .venv-flask/bin/pybabel update -i messages.pot -d app/translations --no-fuzzy-matching
```

Fill in the new `msgstr` in `app/translations/ar/LC_MESSAGES/messages.po`, then:

```bash
.venv-flask/bin/pybabel compile -d app/translations && rm messages.pot
```

`.mo` files are gitignored, so **compile is part of deployment**. Skip it and
gettext silently falls back to the English source string — tests pass, and every
Arabic screen is quietly wrong. `TestLocalisationEndToEnd` guards against this.

**Restart the dev server after compiling.** The reloader watches `.py`, not
`.mo`, so a freshly compiled catalogue is not picked up until the process
restarts — which looks exactly like a missing translation.

**Never name a template directory with a leading underscore.** Babel skips
those by default, so the strings inside are never extracted and never appear in
the catalogue at all. This bit us once: every shared macro lived in
`_partials/` and silently rendered in English. `tests/test_i18n_coverage.py`
now fails if it happens again.

## Layout

```
app/
  config.py          Dev / Test / Prod configs; Prod fails loudly on missing secrets
  extensions.py      Extension singletons + SQLAlchemy declarative Base
  gates.py           before_request walls: password change, then terms acceptance
  decorators.py      require_role / require_staff
  i18n.py            Locale selection and RTL
  labels.py          Translatable labels for enum values (never translate a .value)
  models/            User, profiles, ParentLink, Terms*, Course*, Session*,
                     Homework/Feedback/Material, AuditLog
  services/          ALL business rules and authorization live here
  blueprints/        main, auth, admin, assistant, teacher, portal
  seeds/terms_v1.py  Contractual Arabic terms text, verbatim
tests/               conftest fixtures give one logged-in client per role
```

## The rules that matter

Full list in PLAN.md §7. The three that bite hardest if ignored:

1. **Route handlers never query models directly.** Call a `services/` function
   that takes the acting user first and can only return rows they may see.
2. **404 for an out-of-scope row, 403 for a role-gated area.** On a row the ID is
   the secret; a 403 would confirm it exists.
3. **No bare `left`/`right` in CSS.** Logical properties only, or Arabic breaks.
   `TestStylesheet` enforces this.
4. **Never write `_(something.value)`.** Extraction is static, so a runtime
   value produces no msgid and renders in English forever. Enum text goes in
   `app/labels.py`; `tests/test_i18n_coverage.py` enforces this.
5. **Session dates/times are naive local wall clock; event timestamps are aware
   UTC** via `UtcDateTime`. Mixing them up shifts half the year by an hour.
6. **Course visibility is not enough for feedback.** It is private to one student
   and their parents — always read it through `scoping.feedback_for`, never a
   bare query.

## WhatsApp

**There is no API integration, and sending is manual.** The app composes each
student's daily update and the assistant clicks **Send on WhatsApp**, which
records the hand-off and opens WhatsApp with the text already typed. They press
send there.

No Meta account, no access token, no webhook, no waiting on business
verification. The trade, stated where nobody can miss it:

- **Nothing sends on a schedule.** No cron, no timer. If nobody opens
  `/assistant/whatsapp`, no family hears anything.
- **One click per student.** There is no bulk send from a personal account.
- **`sent` means a named assistant opened it at a recorded time** — not that it
  was delivered or read. The app cannot see past the hand-off and never claims
  to.
- Messages come from whoever sent them, not from the centre's number.

Optionally write the day's texts ahead of time (idempotent — running twice
prepares once). This **sends nothing**; each message still needs the button:

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask prepare-daily-updates
```

Full cost accounting in [ACCEPTANCE.md §9.6](docs/ACCEPTANCE.md).

## Before go-live

One thing needs a person, not code:

1. **A native Arabic review.** Every Arabic string was written by the developer.
   Export them for someone to correct in a spreadsheet:

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask export-translations
```

## Audit trail

Every mutating route is classified in `ROUTE_AUDIT_REGISTRY`
(`tests/test_audit.py`) as either the audit action it emits or `NO_AUDIT` with a
written reason. **Adding a mutating route without classifying it fails the test
suite** — that is what stops coverage rotting. Admin reads the trail at
`/admin/audit`; nothing anywhere can edit or delete it.

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask audit-stats
```

Retention is unanswered (open question 13), so `flask archive-audit
--older-than-days N` exports to JSONL and **deletes nothing**.

## Security

- **CSP** with `script-src 'self'`, no inline script, and no remote origin in
  any fetch directive. Set in `app/security.py`. Two deliberate loosenings, both
  tested: `style-src` allows inline attributes, and `form-action` lists
  `https://wa.me` because the send button redirects there after recording.
- **Login throttle** — 10 failed attempts per IP per 15 minutes
  (`LOGIN_MAX_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`). Counted from the audit table
  so several gunicorn workers share one quota. Per-IP, not per-account, so
  nobody can lock a teacher out of their own timetable.
- **`tests/test_security_hardening.py` walks every route in the app.** A new
  endpoint that serves an anonymous caller, or reaches across roles, fails the
  suite.

Backups (Postgres, production):

```bash
15 2 * * *  BACKUP_REMOTE=user@host:/backups /srv/mca/scripts/backup.sh >> /var/log/mca-backup.log 2>&1
```

Restore drills — run monthly and after any schema change. The dev one has been
run; **the Postgres one has not, because there is no Postgres server yet**:

```bash
./scripts/restore-drill-sqlite.sh
```

## Deploying

Everything is written and tested; it needs a host. Full instructions in
[docs/RUNBOOK.md](docs/RUNBOOK.md). Target setup is one VPS with Postgres on the
same box.

**Before creating the real database**, verify Postgres — every migration so far
has only ever run against SQLite:

```bash
./scripts/verify-postgres.sh
```

Then, on every deploy:

```bash
./scripts/deploy.sh
```

It backs up before migrating, compiles translations, migrates, and **gates the
restart on `flask preflight`** — 14 checks that refuse a misconfigured release.
Rollback is code-only and never downgrades the database.

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask preflight
```

```bash
FLASK_APP=wsgi.py .venv-flask/bin/flask integrity-check
```

## Not yet built

Teacher calendar editing (a deliberate deviation — see
[ACCEPTANCE.md §9.1](docs/ACCEPTANCE.md)), teacher self check-in, payout
reports, refunds, and public trial booking. All are explained in
[HANDOVER.md](docs/HANDOVER.md).
