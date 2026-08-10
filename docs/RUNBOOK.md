# Runbook

Operating instructions for MCA Academy in production. Written for whoever is
on the end of the phone when something breaks, not for whoever wrote it.

---

## 1. First-time provisioning (S10.1)

**These steps need your accounts and money, so they are yours to run, not the
developer's.** Everything after §2 is scripted.

**Chosen setup: one VPS with Postgres on the same box.**

1. **Host** — one small VPS is enough for a few hundred users. 2 vCPU / 4 GB.
2. **Postgres on the same box.**

```bash
sudo apt install postgresql postgresql-contrib
sudo -u postgres createuser --pwprompt mca
sudo -u postgres createdb --owner=mca mca
```

   Put the URL in `.env` as
   `DATABASE_URL=postgresql+psycopg://mca:PASSWORD@localhost:5432/mca`.

   **Then verify before creating anything real** — every migration so far has
   only ever run on SQLite:

```bash
cd /srv/mca && ./scripts/verify-postgres.sh
```

   That runs the whole migration chain up, down and up again against a
   throwaway database, then runs the full test suite against Postgres. Do this
   *before* §2, while a mistake costs nothing.
3. **Domain and TLS** — point a record at the host, then
   `sudo certbot --nginx -d your.domain`.
4. **App user and directory**

```bash
sudo adduser --system --group --home /srv/mca mca
sudo -u mca git clone <repo> /srv/mca
sudo -u mca python3.12 -m venv /srv/mca/.venv
sudo -u mca /srv/mca/.venv/bin/pip install -r /srv/mca/requirements.txt
```

5. **Secrets** — copy `.env.example` to `/srv/mca/.env`, `chmod 600`, and fill in.
   Generate the key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

6. **Services**

```bash
sudo cp /srv/mca/deploy/mca.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mca
sudo cp /srv/mca/deploy/nginx.conf /etc/nginx/sites-available/mca
sudo ln -s /etc/nginx/sites-available/mca /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
```

7. **Backups** — `15 2 * * * BACKUP_REMOTE=user@host:/backups /srv/mca/scripts/backup.sh`

> **Uploads live on local disk** (`instance/uploads/`). On a single VPS that is
> fine and the backup script does not cover them — add them to your file backup,
> or move to S3-compatible storage, which is a one-file change in
> `app/services/storage.py`.

---

## 2. Go-live data (S10.3)

Run **on the server**, once:

```bash
cd /srv/mca && export FLASK_APP=wsgi.py
sudo -u mca .venv/bin/flask db upgrade
sudo -u mca .venv/bin/flask seed-course-types
sudo -u mca .venv/bin/flask seed-terms
sudo -u mca .venv/bin/flask create-admin --name "Center Owner" --phone 01XXXXXXXXX
```

`create-admin` prints a password **once**. Write it down; it is not
recoverable. Sign in and change it immediately.

**Never run `flask seed-demo` on production.** It creates an admin with a
publicly known password. It refuses to run outside debug/testing, and
`flask preflight` warns if that account exists.

Confirm before announcing the URL to anyone:

```bash
sudo -u mca .venv/bin/flask preflight
```

Every line must be `ok`.

---

## 3. Deploying a change

```bash
cd /srv/mca && sudo -u mca ./scripts/deploy.sh
```

It backs up first, installs, compiles translations, migrates, runs preflight,
then restarts — and aborts before restarting if preflight fails.

### Rolling back

```bash
cd /srv/mca && sudo -u mca ./scripts/rollback.sh
```

**Code only.** It does not reverse migrations, on purpose: `db downgrade` drops
columns on a live database and destroys data the older code merely would not
have displayed. If you must go back past a migration, restore the pre-deploy
backup instead — which is why the deploy takes one first.

---

## 4. Backups and restores

| | |
|---|---|
| Nightly | `scripts/backup.sh` — dumps, verifies the archive, prunes, copies off-box |
| Drill | `scripts/restore-drill.sh` — restores into a scratch DB, checks row counts and migration version |
| Dev equivalent | `scripts/restore-drill-sqlite.sh` |

**Run the drill monthly and after any schema change.** A backup nobody has
restored is a file, not a backup.

### Actually restoring

```bash
sudo systemctl stop mca
dropdb mca && createdb mca
pg_restore --no-owner --no-privileges --dbname=mca /var/backups/mca/mca-<stamp>.dump
sudo -u mca FLASK_APP=wsgi.py .venv/bin/flask preflight
sudo systemctl start mca
```

---

## 5. When something is wrong

| Symptom | First thing to check |
|---|---|
| Site down | `systemctl status mca`, then `journalctl -u mca -n 100` |
| 502 from nginx | gunicorn is down or the socket moved — `systemctl restart mca` |
| Everything in English | `.mo` files missing. `pybabel compile -d app/translations` then restart |
| "Too many failed attempts" | The login throttle. 10 failures per IP per 15 minutes; it clears itself |
| Nobody got a WhatsApp update | Nothing sends itself. Someone has to open `/assistant/whatsapp` and press the button per student |
| A message says "prepared" | It was written and never opened in WhatsApp. The family has heard nothing |
| The send button opens a blank chat | The recipient's phone is not on WhatsApp, or is stored without its country code |
| Attendance times look wrong | Check the server clock and `TIMEZONE` (`Africa/Cairo`) |
| Slow after months of use | `flask audit-stats`; consider `flask archive-audit` |

Health check: `curl -fsS https://your.domain/healthz` → `{"status":"ok"}`

Logs: `journalctl -u mca -f`. There is no second unit — nothing sends on a
schedule any more.

---

## 6. Staff walkthrough (S10.4)

Do this **with the admin and one assistant present**, on production, before
announcing the URL. Each step is a thing they will do weekly.

1. **Admin signs in** and is forced to change the generated password.
2. **Admin creates the assistant account.** Note that the password shows once —
   have them write it down. Then use "Issue new password" so they see the
   recovery path exists.
3. **Assistant signs in**, changes password.
4. **Assistant creates a teacher**, then a student *with a parent*. Try a family
   where student and parent share one phone, and read what the app says.
5. **Assistant creates a course** — pick the type, set the teacher, set two
   weekly slots, set a start date and price.
6. **Deliberately cause a clash**: create a second course for the same teacher
   overlapping the first. Confirm the error names the course and time.
7. **Generate sessions**, and look at the round in the sessions list.
8. **Enrol the student**, then mark them paid on the Payments page.
9. **Mark attendance** for today's session, including one student late.
10. **Write homework and feedback.** Feedback is private — check it on the
    parent's login afterwards.
11. **Parent signs in** on a phone and finds: next session, attendance, homework,
    their own child's feedback only.
12. **WhatsApp centre** — read the composed message, press **Send on WhatsApp**,
    and send it for real from the tab that opens. Make sure they understand the
    app does not send by itself, and that "prepared" means nobody has.
13. **Admin opens the audit log** and finds the attendance change from step 9,
    with who made it and when.

Write down anything that made either of them hesitate. Hesitation is the bug
report; they will not file one.

---

## 7. Known limitations at go-live

- **WhatsApp sending is manual, by design.** The app composes each update and
  opens WhatsApp with the text already typed; an assistant presses send from
  their own account. Nothing sends on a schedule, there is no bulk send, and
  the app cannot tell you whether a message arrived — `sent` means a named
  assistant opened it at a recorded time, nothing more. Messages come from
  whichever assistant sent them, not from 01559306667. Full reasoning and what
  this cost in [ACCEPTANCE.md §9.6](ACCEPTANCE.md).
- **Arabic has not been reviewed by a native speaker.** Run
  `flask export-translations` and have someone correct the CSV.
- **The Postgres backup and restore scripts have never been executed**, because
  development used SQLite. Run `scripts/restore-drill.sh` on the real server
  before you rely on it — ideally the same day you provision, not the first
  time you need it.
- Open questions 6, 8–13 in `PLAN.md §8` are unanswered; the features that
  depend on them (refunds, payout reports, public trial booking, teacher
  self check-in, audit retention) are not built.
