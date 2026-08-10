"""Management commands."""

from __future__ import annotations

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db
from app.models.enums import Role, TermsAudience
from app.models.terms import TermsVersion
from app.models.user import User
from app.seeds.terms_v1 import STUDENT_PARENT_TERMS_AR, TEACHER_TERMS_AR
from app.services import accounts as accounts_service
from app.services import terms as terms_service
from app.services.auth import generate_password, hash_password, normalise_phone


def register_cli(app) -> None:
    app.cli.add_command(seed_terms)
    app.cli.add_command(seed_course_types)
    app.cli.add_command(create_admin)
    app.cli.add_command(seed_demo)
    app.cli.add_command(generate_sessions)
    app.cli.add_command(prepare_daily_updates)
    app.cli.add_command(export_translations)
    app.cli.add_command(audit_stats)
    app.cli.add_command(archive_audit)
    app.cli.add_command(preflight)
    app.cli.add_command(integrity_check)


@click.command("seed-course-types")
@with_appcontext
def seed_course_types():
    """Insert the six fixed course types. Idempotent; safe on every deploy."""
    from app.services.courses import seed_course_types as _seed

    created = _seed()
    if created:
        click.secho(f"Seeded {created} course type(s).", fg="green")
    else:
        click.echo("Course types already present, nothing to do.")


@click.command("seed-terms")
@with_appcontext
def seed_terms():
    """Publish terms v1 for both audiences if they are not already present.

    Idempotent, so it is safe on every deploy.
    """
    seeds = {
        TermsAudience.TEACHER: TEACHER_TERMS_AR,
        TermsAudience.STUDENT_PARENT: STUDENT_PARENT_TERMS_AR,
    }
    for audience, body in seeds.items():
        exists = db.session.scalar(
            select(TermsVersion.id).where(TermsVersion.audience == audience)
        )
        if exists:
            click.echo(f"  {audience.value}: already seeded, skipped")
            continue
        version = terms_service.publish_version(None, audience, body_ar=body)
        click.echo(f"  {audience.value}: published v{version.version}")
    click.secho("Terms seeding complete.", fg="green")


@click.command("create-admin")
@click.option("--name", prompt="Full name", help="Admin full name.")
@click.option("--phone", prompt="Phone number", help="Login phone, e.g. 01000000001.")
@with_appcontext
def create_admin(name: str, phone: str):
    """Create the pre-provisioned admin account.

    The password is generated and printed once. It is never stored in plaintext
    and cannot be recovered — write it down before closing this terminal.
    """
    normalised = normalise_phone(phone)
    if db.session.scalar(select(User).where(User.phone == normalised)):
        raise click.ClickException(f"{phone} already belongs to an account.")

    plaintext = generate_password()
    admin = User(
        role=Role.ADMIN,
        full_name=name.strip(),
        phone=normalised,
        username=normalised,
        password_hash=hash_password(plaintext),
        must_change_password=True,
    )
    db.session.add(admin)
    db.session.commit()

    click.echo("")
    click.secho("  Admin account created", fg="green", bold=True)
    click.echo(f"  Username : {normalised}")
    click.secho(f"  Password : {plaintext}", fg="yellow", bold=True)
    click.echo("")
    click.secho("  Shown once. Change it at first sign-in.", fg="red")


@click.command("seed-demo")
@with_appcontext
def seed_demo():
    """Development-only sample data. Never run this on production."""
    from flask import current_app

    if not current_app.debug and not current_app.testing:
        raise click.ClickException("seed-demo refuses to run outside development.")

    if db.session.scalar(select(User.id).where(User.role == Role.ADMIN)):
        click.echo("Users already exist; skipping demo seed.")
        return

    admin = User(
        role=Role.ADMIN,
        full_name="Center Owner",
        phone="+201000000001",
        username="+201000000001",
        password_hash=hash_password("admin1234"),
        must_change_password=False,
    )
    db.session.add(admin)
    db.session.commit()

    assistant = accounts_service.create_assistant(admin, "Mona Assistant", "01000000002")
    teacher = accounts_service.create_teacher(admin, "Ahmed Fathy", "01011112222", "Math")
    family = accounts_service.create_student_with_parent(
        admin,
        student_name="Youssef Adel",
        student_phone="01055556666",
        parent_name="Adel Mostafa",
        parent_phone="01099990000",
    )

    click.secho("Demo accounts created:", fg="green", bold=True)
    click.echo("  admin      +201000000001 / admin1234")
    for result in (assistant, teacher, family):
        for account in result.accounts:
            if account.has_login:
                click.echo(
                    f"  {account.label.lower():<10} {account.user.username} "
                    f"/ {account.plaintext_password}"
                )
            else:
                click.echo(f"  {account.label.lower():<10} {account.user.full_name} (no login)")


@click.command("generate-sessions")
@click.option("--course-id", type=int, default=None, help="Limit to one course.")
@with_appcontext
def generate_sessions(course_id: int | None):
    """Create any missing dated sessions for active courses. Idempotent."""
    from app.models.course import Course
    from app.models.enums import CourseStatus
    from app.services import sessions as session_service

    query = select(Course).where(Course.status == CourseStatus.ACTIVE)
    if course_id:
        query = query.where(Course.id == course_id)

    total = 0
    for course in db.session.scalars(query):
        try:
            created = session_service.generate_sessions(None, course)
        except session_service.SessionError as exc:
            click.secho(f"  skipped {course.name}: {exc}", fg="yellow")
            continue
        if created:
            click.echo(f"  {course.name}: {len(created)} session(s)")
            total += len(created)
    click.secho(f"Created {total} session(s).", fg="green")


@click.command("prepare-daily-updates")
@click.option("--date", "on_raw", default=None, help="YYYY-MM-DD; defaults to today.")
@with_appcontext
def prepare_daily_updates(on_raw: str | None):
    """Compose each student's daily update. Idempotent per student/day.

    **Sends nothing.** There is no WhatsApp API here: every message still needs
    an assistant to open it from the message centre and press send. This exists
    so the day's texts can be written ahead of time — by cron before the centre
    opens, if you like — and it is safe to run twice.
    """
    from datetime import date as _date

    from app.services import whatsapp as whatsapp_service

    on = _date.fromisoformat(on_raw) if on_raw else _date.today()
    counters = whatsapp_service.prepare_daily_batch(None, on)

    click.echo(f"  date     : {on}")
    click.echo(f"  prepared : {counters['prepared']}")
    click.echo(f"  skipped  : {counters['skipped']}")
    click.secho(
        "  Nothing was sent — open /assistant/whatsapp to send them.", fg="yellow"
    )


@click.command("export-translations")
@click.option("--out", default="translations-review.csv", help="Where to write the file.")
@with_appcontext
def export_translations(out: str):
    """Write the Arabic catalogue to a CSV for a native speaker to review.

    Every Arabic string in this project was written by the developer, not a
    native speaker. This makes the review a concrete task someone can do in a
    spreadsheet: read the English, correct the Arabic, hand the file back.
    Corrections go into app/translations/ar/LC_MESSAGES/messages.po, then
    `pybabel compile`.
    """
    import csv
    import re
    from pathlib import Path

    po = Path("app/translations/ar/LC_MESSAGES/messages.po")
    if not po.exists():
        raise click.ClickException(f"{po} not found.")

    chunk = r'"(?:[^"\\]|\\.)*"'

    def unescape(block: str) -> str:
        raw = "".join(part[1:-1] for part in re.findall(chunk, block))
        return raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")

    text = po.read_text(encoding="utf-8")
    pattern = re.compile(rf"^msgid ((?:{chunk}\n?)+)msgstr ((?:{chunk}\n?)+)", re.M)

    rows = []
    for match in pattern.finditer(text):
        english = unescape(match.group(1))
        arabic = unescape(match.group(2))
        if english:
            rows.append({"english": english, "current_arabic": arabic, "corrected_arabic": ""})

    with open(out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["english", "current_arabic", "corrected_arabic"]
        )
        writer.writeheader()
        writer.writerows(rows)

    click.secho(f"Wrote {len(rows)} strings to {out}", fg="green")
    click.echo("  Give it to a native Arabic reader; they fill in 'corrected_arabic'.")


@click.command("audit-stats")
@with_appcontext
def audit_stats():
    """How much audit history exists, and how far back (sprint S8.4)."""
    from sqlalchemy import func

    from app.models.audit import AuditLog

    total = db.session.scalar(select(func.count(AuditLog.id)))
    oldest = db.session.scalar(select(func.min(AuditLog.created_at)))
    newest = db.session.scalar(select(func.max(AuditLog.created_at)))

    click.echo(f"  entries : {total}")
    click.echo(f"  oldest  : {oldest or '—'}")
    click.echo(f"  newest  : {newest or '—'}")

    by_action = db.session.execute(
        select(AuditLog.action, func.count(AuditLog.id))
        .group_by(AuditLog.action)
        .order_by(func.count(AuditLog.id).desc())
        .limit(10)
    ).all()
    if by_action:
        click.echo("  busiest actions:")
        for action, count in by_action:
            click.echo(f"    {count:>6}  {action}")


@click.command("archive-audit")
@click.option("--older-than-days", type=int, required=True, help="Age cutoff in days.")
@click.option("--out", default="audit-archive.jsonl", help="Where to write the export.")
@with_appcontext
def archive_audit(older_than_days: int, out: str):
    """Export audit entries older than N days to a file.

    **Exports only — it deletes nothing.** Retention is open question 13 in
    PLAN.md, and until the centre says how long these must be kept, silently
    destroying the record of who changed attendance or money would be the
    wrong default. Once the policy is set, deletion can be added here on top
    of a verified archive.
    """
    import json
    from datetime import timedelta

    from app.models.audit import AuditLog
    from app.models.base import utcnow

    cutoff = utcnow() - timedelta(days=older_than_days)
    rows = db.session.scalars(
        select(AuditLog).where(AuditLog.created_at < cutoff).order_by(AuditLog.id)
    ).all()

    if not rows:
        click.echo(f"Nothing older than {older_than_days} days.")
        return

    with open(out, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "id": row.id,
                        "created_at": row.created_at.isoformat(),
                        "actor_id": row.actor_id,
                        "action": row.action,
                        "entity_type": row.entity_type,
                        "entity_id": row.entity_id,
                        "before": row.before_json,
                        "after": row.after_json,
                        "ip": row.ip,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    click.secho(f"Exported {len(rows)} entries to {out}", fg="green")
    click.secho(
        "  Nothing was deleted. Retention is open question 13 — see PLAN.md §8.",
        fg="yellow",
    )


@click.command("preflight")
@with_appcontext
def preflight():
    """Check the app is fit to serve traffic. Exits non-zero on any failure."""
    from flask import current_app

    from app.preflight import Level, run_all, worst_level

    checks = run_all(current_app)
    symbols = {Level.PASS: ("  ok  ", "green"), Level.WARN: (" warn ", "yellow"),
               Level.FAIL: (" FAIL ", "red")}

    for check in checks:
        mark, colour = symbols[check.level]
        click.secho(f"[{mark}]", fg=colour, nl=False)
        click.echo(f" {check.name}" + (f" — {check.detail}" if check.detail else ""))

    worst = worst_level(checks)
    click.echo("")
    if worst is Level.FAIL:
        click.secho("Preflight FAILED. Do not serve traffic.", fg="red", bold=True)
        raise SystemExit(1)
    if worst is Level.WARN:
        click.secho("Preflight passed with warnings.", fg="yellow")
    else:
        click.secho("Preflight passed.", fg="green", bold=True)


@click.command("integrity-check")
@with_appcontext
def integrity_check():
    """Look for orphaned or inconsistent data. Read-only; never repairs."""
    from app.integrity import run_all

    findings = run_all()
    if not findings:
        click.secho("No integrity problems found.", fg="green", bold=True)
        return

    for finding in findings:
        click.secho(f"  [{finding.count:>4}] {finding.check}", fg="red")
        if finding.detail:
            click.echo(f"         {finding.detail}")
        if finding.examples:
            click.echo(f"         example ids: {finding.examples}")

    click.echo("")
    click.secho(
        f"{len(findings)} problem type(s) found. Nothing was changed — decide the "
        "right fix for each before touching real records.",
        fg="yellow",
    )
    raise SystemExit(1)
