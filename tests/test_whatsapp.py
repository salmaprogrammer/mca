"""Sprints S6.1–S6.6 — daily updates, sent by hand.

There is no WhatsApp API. The app composes each update, records it, and builds
a click-to-chat link the assistant opens in their own WhatsApp. The two things
most worth defending:

* **Honesty about delivery.** "prepared" and "sent" are different facts and the
  system must never blur them — a family whose update was written and never
  opened has heard nothing, and staff would tell a parent otherwise.
* **Idempotency.** Preparing twice must not give a family two updates.
"""

from __future__ import annotations

import urllib.parse
from datetime import date

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.enums import AttendanceStatus, MessageStatus, Role
from app.models.messaging import WhatsAppMessage
from app.services import attendance as attendance_service
from app.services import enrollments as enrollment_service
from app.services import sessions as session_service
from app.services import teaching as teaching_service
from app.services import whatsapp as whatsapp_service
from tests.conftest import accept_current_terms, link_parent, login, make_course, make_user

SUNDAY = 6
WEDNESDAY = 2
START = date(2026, 9, 6)


@pytest.fixture
def world(app, db, seeded_terms, seeded_course_types, admin):
    teacher = make_user(Role.TEACHER, phone="+201011112001", name="Ahmed")
    accept_current_terms(teacher)
    student = make_user(Role.STUDENT, phone="+201055552001", name="Youssef")
    accept_current_terms(student)
    parent = make_user(Role.PARENT, phone="+201099992001", name="Adel")
    link_parent(parent, student)
    accept_current_terms(parent)

    course = make_course(
        admin,
        teacher=teacher,
        name="Nov round",
        course_type_code="gpa_course",
        slots=[
            {"weekday": SUNDAY, "start_time": "16:00"},
            {"weekday": WEDNESDAY, "start_time": "16:00"},
        ],
        start_date=START,
    )
    enrollment_service.enroll(admin, course, student)
    session_service.generate_sessions(admin, course)

    return {
        "admin": admin,
        "teacher": teacher,
        "student": student,
        "parent": parent,
        "course": course,
    }


class TestSummaryBuilder:
    def test_it_includes_all_three_sections(self, app, world):
        session = world["course"].sessions[0]
        attendance_service.mark_student(
            world["admin"], session, world["student"], AttendanceStatus.PRESENT
        )
        teaching_service.add_homework(
            world["admin"], world["course"], text="Worksheet 3", for_date=START
        )
        teaching_service.add_feedback(
            world["admin"], world["course"], world["student"],
            text="Strong on algebra", for_date=START,
        )

        body = whatsapp_service.build_daily_summary(world["student"], START, locale="en")
        assert "Youssef" in body
        assert "Worksheet 3" in body
        assert "Strong on algebra" in body
        assert "present" in body.lower()

    def test_empty_sections_say_so_rather_than_vanishing(self, app, world):
        """A message with a missing section looks broken to a parent."""
        body = whatsapp_service.build_daily_summary(world["student"], START, locale="en")
        assert "No session recorded today." in body
        assert "None assigned today." in body
        assert "None logged today." in body

    def test_one_message_covers_every_course(self, app, db, world, admin):
        """Open question 7, answered: combined, not one message per course."""
        second = make_course(
            admin,
            teacher=world["teacher"],
            name="Second course",
            course_type_code="sat_intermediate",
            slots=[{"weekday": 0, "start_time": "09:00"}],
            start_date=START,
        )
        enrollment_service.enroll(admin, second, world["student"])
        teaching_service.add_homework(
            admin, world["course"], text="FIRSTCOURSEHW", for_date=START
        )
        teaching_service.add_homework(
            admin, second, text="SECONDCOURSEHW", for_date=START
        )

        body = whatsapp_service.build_daily_summary(world["student"], START, locale="en")
        assert "FIRSTCOURSEHW" in body
        assert "SECONDCOURSEHW" in body

    def test_it_renders_in_the_recipients_language(self, app, world):
        arabic = whatsapp_service.build_daily_summary(world["student"], START, locale="ar")
        assert "التحديث اليومي" in arabic or "Youssef" in arabic
        assert "No session recorded today." not in arabic

    def test_a_student_in_no_course_still_gets_a_coherent_message(self, app, db, world):
        loner = make_user(Role.STUDENT, phone="+201055552099", name="Solo")
        body = whatsapp_service.build_daily_summary(loner, START, locale="en")
        assert "Solo" in body

    def test_another_students_feedback_never_appears(self, app, db, world, admin):
        """The family boundary holds inside the message body too."""
        classmate = make_user(Role.STUDENT, phone="+201055552002", name="Nour")
        enrollment_service.enroll(admin, world["course"], classmate)
        teaching_service.add_feedback(
            admin, world["course"], classmate, text="CLASSMATESECRET", for_date=START
        )
        body = whatsapp_service.build_daily_summary(world["student"], START, locale="en")
        assert "CLASSMATESECRET" not in body


class TestChatLink:
    """The link is the whole delivery mechanism, so its shape is load-bearing."""

    def test_it_carries_the_number_and_the_text(self, app, world):
        message = whatsapp_service.prepare_daily_update(
            world["admin"], world["student"], on=START
        )
        link = whatsapp_service.chat_link(message)
        assert link.startswith("https://wa.me/201099992001?text=")
        assert urllib.parse.quote("Youssef") in link

    def test_the_number_loses_its_plus(self, app, world):
        """wa.me takes bare digits; a `+` opens a chat with nobody."""
        message = whatsapp_service.prepare_daily_update(
            world["admin"], world["student"], on=START
        )
        assert "+" not in link_number(whatsapp_service.chat_link(message))

    def test_arabic_survives_the_round_trip(self, app, db, world):
        """The body is Arabic by default; a mangled encoding is unreadable."""
        message = whatsapp_service.prepare_daily_update(
            world["admin"], world["student"], on=START
        )
        text = urllib.parse.parse_qs(
            urllib.parse.urlparse(whatsapp_service.chat_link(message)).query
        )["text"][0]
        assert text == message.body


def link_number(link: str) -> str:
    return urllib.parse.urlparse(link).path.lstrip("/")


class TestSendingIsManual:
    """The system knows a person clicked. It must not claim more than that."""

    def test_preparing_does_not_mark_anything_sent(self, app, world):
        message = whatsapp_service.prepare_daily_update(
            world["admin"], world["student"], on=START
        )
        assert message.status is MessageStatus.PREPARED
        assert message.sent_at is None
        assert message.sent_by_id is None
        assert message.actually_left_the_building is False

    def test_the_hand_off_records_who_and_when(self, app, world):
        message = whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        assert message.status is MessageStatus.SENT
        assert message.sent_by_id == world["admin"].id
        assert message.sent_at is not None

    def test_the_hand_off_reuses_the_prepared_text(self, app, db, world):
        """The assistant must send the text they read, not a recomposition.

        Editing homework between preparing and sending would otherwise silently
        change the message under them.
        """
        prepared = whatsapp_service.prepare_daily_update(
            world["admin"], world["student"], on=START
        )
        original = prepared.body
        teaching_service.add_homework(
            world["admin"], world["course"], text="ADDEDLATER", for_date=START
        )

        sent = whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        assert sent.id == prepared.id
        assert sent.body == original
        assert "ADDEDLATER" not in sent.body

    def test_sending_without_preparing_first_works(self, app, world):
        """One click is the common path; preparing ahead is optional."""
        message = whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        assert message.batch_date == START
        assert message.status is MessageStatus.SENT

    def test_there_is_no_provider_module_left(self):
        """The Cloud API is gone, not merely unused — an unreachable sender
        that still compiles is the thing somebody re-enables by accident."""
        with pytest.raises(ImportError):
            import app.services.whatsapp_providers  # noqa: F401

    def test_no_config_key_promises_an_api(self, app):
        leftovers = [
            key
            for key in app.config
            if key.startswith("WHATSAPP_") and key != "WHATSAPP_LINK_BASE"
        ]
        assert leftovers == []


class TestIdempotency:
    def test_a_second_preparation_on_the_same_day_is_refused(self, app, world):
        whatsapp_service.prepare_daily_update(world["admin"], world["student"], on=START)
        with pytest.raises(whatsapp_service.WhatsAppError):
            whatsapp_service.prepare_daily_update(
                world["admin"], world["student"], on=START
            )

    def test_running_the_batch_twice_prepares_once(self, app, db, world):
        first = whatsapp_service.prepare_daily_batch(None, START)
        second = whatsapp_service.prepare_daily_batch(None, START)

        assert first["prepared"] == 1
        assert second["prepared"] == 0
        assert second["skipped"] >= 1

        count = db.session.scalar(
            select(db.func.count(WhatsAppMessage.id)).where(
                WhatsAppMessage.student_id == world["student"].id
            )
        )
        assert count == 1

    def test_sending_twice_over_is_refused_without_force(self, app, world):
        whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        with pytest.raises(whatsapp_service.WhatsAppError):
            whatsapp_service.hand_off(world["admin"], world["student"], on=START)

    def test_a_forced_resend_is_an_extra_not_a_second_daily(self, app, db, world):
        """The re-send carries no batch_date, so the constraint ignores it."""
        first = whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        again = whatsapp_service.hand_off(
            world["admin"], world["student"], on=START, force=True
        )
        assert first.batch_date == START
        assert again.batch_date is None

        # The daily slot is still claimed, so the batch still skips.
        assert whatsapp_service.prepare_daily_batch(None, START)["prepared"] == 0

    def test_different_days_are_separate(self, app, world):
        whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        later = whatsapp_service.hand_off(
            world["admin"], world["student"], on=date(2026, 9, 9)
        )
        assert later.batch_date == date(2026, 9, 9)

    def test_a_student_with_no_contact_number_is_skipped_with_a_reason(
        self, app, db, world
    ):
        orphan = make_user(Role.STUDENT, phone=None, password=None, name="No Phone")
        with pytest.raises(whatsapp_service.WhatsAppError):
            whatsapp_service.prepare_daily_update(world["admin"], orphan, on=START)


class TestRecipient:
    def test_it_goes_to_the_parent_when_there_is_one(self, app, world):
        message = whatsapp_service.prepare_daily_update(
            world["admin"], world["student"], on=START
        )
        assert message.recipient_id == world["parent"].id
        assert message.to_phone == world["parent"].phone

    def test_it_falls_back_to_the_student(self, app, db, world):
        solo = make_user(Role.STUDENT, phone="+201055552050", name="Solo")
        message = whatsapp_service.prepare_daily_update(world["admin"], solo, on=START)
        assert message.recipient_id == solo.id


class TestAuditing:
    def test_preparing_is_audited(self, app, db, world):
        message = whatsapp_service.prepare_daily_update(
            world["admin"], world["student"], on=START
        )
        entry = db.session.scalar(
            select(AuditLog).where(
                AuditLog.action == "whatsapp.prepare",
                AuditLog.entity_id == str(message.id),
            )
        )
        assert entry is not None
        assert entry.actor_id == world["admin"].id

    def test_the_hand_off_names_the_person_who_sent_it(self, app, db, world):
        message = whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        entry = db.session.scalar(
            select(AuditLog).where(
                AuditLog.action == "whatsapp.send",
                AuditLog.entity_id == str(message.id),
            )
        )
        assert entry is not None
        assert entry.actor_id == world["admin"].id

    def test_the_batch_records_no_actor_when_run_headless(self, app, db, world):
        whatsapp_service.prepare_daily_batch(None, START)
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "whatsapp.prepare")
        )
        assert entry.actor_id is None


class TestScoping:
    def test_a_parent_sees_only_their_own_messages(self, app, db, world):
        whatsapp_service.hand_off(world["admin"], world["student"], on=START)

        other_student = make_user(Role.STUDENT, phone="+201055552090", name="Other")
        other_parent = make_user(Role.PARENT, phone="+201099992090", name="OtherParent")
        link_parent(other_parent, other_student)
        whatsapp_service.hand_off(world["admin"], other_student, on=START)

        assert len(whatsapp_service.messages_for(world["parent"])) == 1
        assert (
            whatsapp_service.messages_for(world["parent"])[0].student_id
            == world["student"].id
        )

    def test_a_parent_never_sees_a_message_nobody_sent(self, app, world):
        """Prepared is not sent. Showing it would tell a family they were
        contacted when no one has opened WhatsApp."""
        whatsapp_service.prepare_daily_update(
            world["admin"], world["student"], on=START
        )
        assert whatsapp_service.messages_for(world["parent"]) == []
        assert len(whatsapp_service.messages_for(world["admin"])) == 1

    def test_a_teacher_sees_none(self, app, world):
        whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        assert whatsapp_service.messages_for(world["teacher"]) == []

    def test_staff_see_everything(self, app, world):
        whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        assert len(whatsapp_service.messages_for(world["admin"])) == 1

    def test_a_parent_cannot_request_another_familys_child(self, app, db, world):
        from werkzeug.exceptions import NotFound

        outsider = make_user(Role.STUDENT, phone="+201055552091")
        with pytest.raises(NotFound):
            whatsapp_service.messages_for(world["parent"], student=outsider)


class TestRoutes:
    def test_the_staff_centre_renders_with_a_preview(self, app, world):
        client = app.test_client()
        login(client, world["admin"])
        body = client.get(
            "/assistant/whatsapp", query_string={"date": START.isoformat()}
        ).get_data(as_text=True)
        assert "Youssef" in body

    def test_the_page_explains_that_sending_is_manual(self, app, world):
        """Staff must never believe the app delivered something itself."""
        client = app.test_client()
        login(client, world["admin"])
        client.post("/me/language", data={"locale": "en", "next": "/assistant/whatsapp"})
        body = client.get("/assistant/whatsapp").get_data(as_text=True)
        assert "You still have to press send there" in body

    def test_send_now_records_and_redirects_to_whatsapp(self, app, db, world):
        client = app.test_client()
        login(client, world["admin"])
        response = client.post(
            f"/assistant/whatsapp/open/{world['student'].id}",
            data={"date": START.isoformat()},
        )
        assert response.status_code == 302
        assert response.headers["Location"].startswith("https://wa.me/201099992001?text=")

        message = whatsapp_service.daily_message_for(world["student"], START)
        assert message.status is MessageStatus.SENT

    def test_prepare_all_sends_nothing(self, app, db, world):
        client = app.test_client()
        login(client, world["admin"])
        client.post(
            "/assistant/whatsapp/prepare-all", data={"date": START.isoformat()}
        )
        message = whatsapp_service.daily_message_for(world["student"], START)
        assert message is not None
        assert message.status is MessageStatus.PREPARED

    def test_the_send_form_does_not_open_a_new_tab(self, app, world):
        """`target="_blank"` on a POST is silently downgraded to a GET by
        popup-blocked and embedded browsers, which 405s: nothing recorded,
        nothing opened, and no error the assistant would recognise. Caught in
        manual testing, so it gets a test rather than a comment."""
        client = app.test_client()
        login(client, world["admin"])
        body = client.get("/assistant/whatsapp").get_data(as_text=True)
        assert 'action="/assistant/whatsapp/open/' in body
        assert 'target="_blank"' not in body

    def test_the_csp_allows_the_redirect_it_depends_on(self, app, client):
        """`form-action 'self'` alone would block the wa.me redirect after a
        POST, breaking the only send button with nothing in the logs."""
        policy = client.get("/login").headers["Content-Security-Policy"]
        assert "form-action 'self' https://wa.me" in policy

    def test_a_teacher_cannot_reach_the_message_centre(self, app, world):
        client = app.test_client()
        login(client, world["teacher"])
        assert client.get("/assistant/whatsapp").status_code == 403

    def test_a_parent_cannot_trigger_a_send(self, app, world):
        client = app.test_client()
        login(client, world["parent"])
        response = client.post(
            f"/assistant/whatsapp/open/{world['student'].id}",
            data={"date": START.isoformat()},
        )
        assert response.status_code == 403
        assert whatsapp_service.daily_message_for(world["student"], START) is None

    def test_the_parent_message_history_renders(self, app, world):
        whatsapp_service.hand_off(world["admin"], world["student"], on=START)
        client = app.test_client()
        login(client, world["parent"])
        body = client.get("/portal/messages").get_data(as_text=True)
        assert "Youssef" in body
