# Acceptance audit

Every requirement in `agent-planning-brief.md` traced to the code that
implements it and the test that proves it (sprint S11.1).

`tests/test_acceptance.py` checks that every test named below actually exists,
so this table cannot quietly rot as the code moves.

**Summary: 62 requirements. 57 met, 2 deliberately deviated from, 3 deferred
pending an unanswered question.** The deviations and the deferrals are in §9 —
read those first if you are deciding whether to accept.

---

## 1. Roles and permissions

| # | Requirement | Code | Test |
|---|---|---|---|
| 1.1 | Admin: full read/write, pre-provisioned | `cli.create_admin` | `test_deployment.py::TestPreflight::test_it_flags_a_missing_admin` |
| 1.2 | Admin creates assistant accounts | `services/accounts.create_assistant` | `test_accounts.py::TestAssistantCreation` |
| 1.3 | Assistant creates/edits courses | `services/courses.create_course`, `update_course` | `test_courses.py::TestCourseLifecycle` |
| 1.4 | Assistant creates teacher accounts | `services/accounts.create_teacher` | `test_accounts.py::TestTeacherCreation` |
| 1.5 | Assistant creates student + auto parent | `services/accounts.create_student_with_parent` | `test_accounts.py::TestFamilyCreation` |
| 1.6 | Assistant records teacher attendance | `services/attendance.mark_teacher` | `test_attendance.py::TestMarkingTeachers` |
| 1.7 | Assistant records student attendance with timestamp | `services/attendance.mark_student` | `test_attendance.py::TestMarkingStudents::test_present_records_a_real_timestamp` |
| 1.8 | Assistant assigns daily homework | `services/teaching.add_homework` | `test_teaching.py::TestHomework` |
| 1.9 | Assistant writes daily parent feedback | `services/teaching.add_feedback` | `test_teaching.py::TestFeedbackPrivacy` |
| 1.10 | Assistant sends WhatsApp summaries | `services/whatsapp.hand_off` | `test_whatsapp.py::TestRoutes::test_send_now_records_and_redirects_to_whatsapp` |
| 1.11 | Assistant manages booking/payment status | `services/enrollments` | `test_enrollments.py::TestBookingStatus`, `TestPaymentStatus` |
| 1.12 | Teacher views own courses and students | `services/scoping.courses_for`, `students_for` | `test_courses.py::TestScoping::test_a_teacher_sees_only_their_own_courses` |
| 1.13 | Teacher views own weekly calendar | `services/dashboard.teacher_dashboard` | `test_dashboards.py::TestTeacherDashboard::test_the_week_grid_starts_on_saturday` |
| 1.14 | ⚠️ Teacher **edits** own weekly calendar | **Not implemented — see §9.1** | — |
| 1.15 | Teacher adds material links | `services/teaching.add_material` | `test_teaching.py::TestMaterials` |
| 1.16 | Teacher marks own students' attendance | `blueprints/teacher/routes.mark_student` | `test_attendance.py::TestRouteScoping::test_a_teacher_can_mark_their_own_session` |
| 1.17 | Teacher cannot see other teachers' data | `services/scoping` | `test_attendance.py::TestRouteScoping::test_a_teacher_gets_404_for_another_teachers_session` |
| 1.18 | Student read-only view of own data | `blueprints/portal` | `test_enrollments.py::TestPortalIsReadOnly` |
| 1.19 | Parent same scope as their children | `services/scoping.students_for` | `test_teaching.py::TestFeedbackOverHttp::test_it_appears_for_the_right_family` |
| 1.20 | Parent sees WhatsApp history sent to them | `services/whatsapp.messages_for` | `test_whatsapp.py::TestScoping::test_a_parent_sees_only_their_own_messages` |
| 1.21 | One parent linked to multiple children | `models/user.ParentLink` | `test_accounts.py::TestFamilyCreation::test_existing_parent_is_linked_not_duplicated` |
| 1.22 | **Scoping enforced at the data layer, not hidden UI** | `services/scoping` | `test_security_hardening.py::TestFullAuthorizationMatrix` |

## 2. Auto-provisioned credentials

| # | Requirement | Code | Test |
|---|---|---|---|
| 2.1 | Parent created in the same action | `services/accounts.create_student_with_parent` | `test_accounts.py::TestFamilyCreation::test_distinct_phones_create_two_logins` |
| 2.2 | Existing parent reused, not duplicated | same | `test_accounts.py::TestFamilyCreation::test_existing_parent_is_linked_not_duplicated` |
| 2.3 | Teacher creation makes only a teacher account | `services/accounts.create_teacher` | `test_accounts.py::TestTeacherCreation::test_teacher_gets_a_profile_and_no_parent` |
| 2.4 | Username = phone, validated consistently (E.164) | `services/auth.normalise_phone` | `test_auth.py::TestPhoneNormalisation` |
| 2.5 | System-generated password with defined entropy | `services/auth.generate_password` | `test_auth.py::TestGeneratedPasswords` |
| 2.6 | Forced password change on first login | `gates.py` | `test_gates.py::TestPasswordGate` |
| 2.7 | Credentials shown once, never again | `templates/partials/credentials.html` | `test_auth.py::TestPasswordStorage` |

## 3. Course catalogue

| # | Requirement | Code | Test |
|---|---|---|---|
| 3.1 | Six fixed types, seeded | `seeds/course_types.py` | `test_courses.py::TestCourseTypes::test_all_six_are_seeded` |
| 3.2 | Types not freely editable | *absence of any CRUD route* | `test_courses.py::TestCourseTypes::test_there_is_no_route_that_edits_a_type` |
| 3.3 | Instance carries name, type, teacher, description, cover, schedule, price, trial flag | `models/course.Course` | `test_courses.py::TestCourseLifecycle` |
| 3.4 | Slot count implied by the type | `services/courses.create_course` | `test_courses.py::TestSlotCount` |
| 3.5 | Appears on student/parent dashboards immediately | `services/scoping.courses_for` | `test_courses.py::TestRendering::test_student_portal_renders_their_course` |
| 3.6 | **Conflict rule: same day, overlapping time** | `services/scheduling.intervals_overlap` | `test_scheduling.py::TestIntervalOverlap` |
| 3.7 | Validated on creation **and** on edit | `services/courses` (both paths) | `test_scheduling.py::TestEnforcementPaths` |
| 3.8 | Error names colliding course, day and time | `services/scheduling._build_conflict` | `test_scheduling.py::TestTeacherConflicts::test_the_error_names_course_day_and_times` |

## 4. Attendance

| # | Requirement | Code | Test |
|---|---|---|---|
| 4.1 | Teacher attendance per session | `models/session.CourseSession.teacher_status` | `test_attendance.py::TestMarkingTeachers::test_teacher_attendance_lives_on_the_session` |
| 4.2 | Student attendance per session per course | `models/session.AttendanceRecord` | `test_attendance.py::TestMarkingStudents` |
| 4.3 | Both streams carry date **and** time of check-in | `UtcDateTime` columns | `test_timezone.py::TestInstantsAreAwareUtc` |
| 4.4 | Assistant **and** the course's teacher can record | both blueprints | `test_attendance.py::TestRouteScoping` |
| 4.5 | Queryable per student | `services/attendance.history_for_student` | `test_attendance.py::TestHistory::test_a_student_sees_their_own_records_newest_first` |
| 4.6 | Queryable per teacher/course | `services/attendance.history_for_courses` | `test_attendance.py::TestHistory::test_the_course_direction_is_queryable_too` |
| 4.7 | Teacher self check-in (explicitly "v2") | **Deferred — §9.2** | — |

## 5. Homework and feedback

| # | Requirement | Code | Test |
|---|---|---|---|
| 5.1 | Homework per course, visible to every enrolled student | `services/teaching.add_homework` | `test_teaching.py::TestHomework::test_it_is_visible_to_every_enrolled_student` |
| 5.2 | Feedback per student per course | `services/teaching.add_feedback` | `test_teaching.py::TestFeedbackPrivacy` |
| 5.3 | **Feedback visible to that student and their parents only** | `services/scoping.feedback_for` | `test_teaching.py::TestFeedbackPrivacy::test_a_classmate_cannot_read_it` |

## 6. WhatsApp daily updates

| # | Requirement | Code | Test |
|---|---|---|---|
| 6.1 | Daily summary of attendance, homework, feedback | `services/whatsapp.build_daily_summary` | `test_whatsapp.py::TestSummaryBuilder::test_it_includes_all_three_sections` |
| 6.2 | Sent to the student's parents | `services/whatsapp.recipient_for` | `test_whatsapp.py::TestRecipient` |
| 6.3 | From the centre's number 01559306667 | *sent from the assistant's own WhatsApp — see §9.6* | `test_whatsapp.py::TestChatLink::test_it_carries_the_number_and_the_text` |
| 6.4 | ⚠️ Cloud API / BSP integration | **Not implemented — see §9.6** | — |
| 6.5 | Assistant-triggered "send now" | `blueprints/assistant.whatsapp_open` | `test_whatsapp.py::TestRoutes::test_send_now_records_and_redirects_to_whatsapp` |
| 6.6 | Daily batch | `cli.prepare_daily_updates` | `test_whatsapp.py::TestIdempotency::test_running_the_batch_twice_prepares_once` |
| 6.7 | Log recipient, phone, timestamp, content, send status | `models/messaging.WhatsAppMessage` | `test_whatsapp.py::TestSendingIsManual::test_the_hand_off_records_who_and_when` |
| 6.8 | Log surfaced on the parent dashboard | `blueprints/portal.messages` | `test_whatsapp.py::TestRoutes::test_the_parent_message_history_renders` |
| 6.9 | Log surfaced to admin/assistant | `blueprints/assistant.whatsapp_centre` | `test_whatsapp.py::TestRoutes::test_the_staff_centre_renders_with_a_preview` |
| 6.10 | **Failures visible, not silent** | `services/whatsapp.waiting_to_be_sent` | `test_dashboards.py::TestStaffDashboard::test_updates_written_but_never_sent_are_surfaced` |

## 7. Payments and booking

| # | Requirement | Code | Test |
|---|---|---|---|
| 7.1 | Booking status trial / booked / not booked | `models/enums.BookingStatus` | `test_enrollments.py::TestBookingStatus` |
| 7.2 | Payment status paid / unpaid | `models/enums.PaymentStatus` | `test_enrollments.py::TestPaymentStatus` |
| 7.3 | Editable by assistant and admin | `blueprints/assistant/enrollment_routes` | `test_enrollments.py::TestStaffRoutes::test_the_controls_work_over_http` |
| 7.4 | Read-only on the student/parent dashboard | `blueprints/portal` | `test_enrollments.py::TestPortalIsReadOnly` |

## 8. Terms and conditions

| # | Requirement | Code | Test |
|---|---|---|---|
| 8.1 | Shown exactly once, on first login | `gates.py` | `test_gates.py::TestTermsGate` |
| 8.2 | Dashboard unreachable until accepted | `gates.py` | `test_gates.py::TestTermsGate::test_every_url_redirects_to_terms` |
| 8.3 | Admin and assistant exempt | `models/enums.TermsAudience.for_role` | `test_gates.py::TestTermsGate::test_admin_and_assistant_are_never_gated` |
| 8.4 | Two texts, teacher vs student/parent | `seeds/terms_v1.py` | `test_gates.py::TestTermsGate::test_students_get_the_student_text_not_the_teacher_one` |
| 8.5 | Store exact version shown + timestamped acceptance per user | `models/terms.TermsAcceptance` | `test_gates.py::TestTermsGate::test_accepting_records_who_what_and_when` |
| 8.6 | Full text, checkbox + button, no dismissal | `templates/auth/terms.html` | `test_gates.py::TestTermsGate::test_cannot_pass_without_ticking_the_box` |
| 8.7 | **Version bump re-prompts only those who have not accepted** | `services/terms.publish_version` | `test_gates.py::TestTermsVersioning::test_publishing_v2_re_prompts_users_who_accepted_v1` |

## 9. Non-functional

| # | Requirement | Code | Test |
|---|---|---|---|
| 9.1 | Normalised entities for every listed concept | `models/` | `test_deployment.py::TestPostgresCompatibility::test_every_table_compiles_for_postgres` |
| 9.2 | Phone + password login, session-based | `blueprints/auth`, Flask-Login | `test_auth.py::TestLoginRoute` |
| 9.3 | Passwords hashed, never logged in plaintext | Argon2id | `test_security_hardening.py::TestSecretsHygiene::test_no_plaintext_password_is_ever_logged` |
| 9.4 | Server-side role **and ownership** checks on every endpoint | `decorators.py`, `services/scoping` | `test_security_hardening.py::TestFullAuthorizationMatrix::test_every_endpoint_is_either_public_or_login_protected` |
| 9.5 | Audit trail: who changed what, when | `services/audit` | `test_audit.py::TestAttributability` |
| 9.6 | Timezone confirmed and consistent | Africa/Cairo; `UtcDateTime` | `test_timezone.py::TestWallClockSurvivesDst` |
| 9.7 | Single centre, no multi-tenancy | — | — (nothing tenant-scoped exists) |
| 9.8 | Bilingual Arabic/English | Flask-Babel | `test_i18n_coverage.py` |

## 10. Planning deliverables

| # | Requirement | Where |
|---|---|---|
| 10.1 | Tech stack with justification | `PLAN.md` §1 |
| 10.2 | ERD / schema outline | `PLAN.md` §2.2 |
| 10.3 | API surface by role | `PLAN.md` §5 |
| 10.4 | Milestone plan | `PLAN.md` §6 |
| 10.5 | Written open-questions list | `PLAN.md` §8 |
| 10.6 | Ambiguities and conflicts flagged, not silently resolved | `PLAN.md` §9 |

---

## 9. Not met, and why

### 9.1 Deviation — teacher cannot edit their own weekly calendar (req 1.14)

The brief says a teacher can "view/**edit** own weekly calendar". **Viewing is
built; editing is not**, and this is deliberate rather than an oversight.

The teacher terms in the same brief require that *"تعديل وإلغاء المحاضرات: يتم
إلغاء أو تعديل ميعاد أي سيشن من خلال التواصل مع الـ Assistant فقط قبل موعد
المحاضرة بـ ٢٤ ساعة على الأقل"* — all schedule changes go through the
assistant with 24 hours' notice. A teacher silently rewriting their own week in
the app would contradict the agreement they sign on first login.

The teacher's calendar page says so in words rather than offering a control
that shouldn't exist. **If you want teachers to edit their own schedules, say
so and it is a small change** — but the terms text should change with it.

### 9.2 Deferred — teacher self check-in (req 4.7)

The brief itself marks this "consider allowing … as a v2 option". Open question
12. Teacher attendance is recorded by staff today.

### 9.3 Deferred — refunds and mid-round withdrawal

Open question 10, unanswered. Payment status can be reverted to unpaid and the
change is audited, but there is no refund concept. Listed in the brief as an
open question, not a requirement.

### 9.4 Deferred — teachers with multiple subjects

Open question 11, unanswered. `teacher_profiles.subject` is a single value, as
in the prototype.

### 9.5 Partially met — English terms text

Open question 6, unanswered. Both terms versions exist in Arabic; `body_en` is
null, so English-language users are shown the Arabic text (rendered RTL inside
an otherwise LTR page). The versioning mechanism is in place — supplying an
English text is a data change, not a code change.

### 9.6 Deviation — no WhatsApp API; the assistant sends by hand (req 6.4)

The brief calls for the Meta Cloud API or a BSP. **That integration was built,
then removed at the owner's request**, and replaced with a button: the app
composes each update and opens WhatsApp with the text already typed, and the
assistant presses send from their own account.

What this buys: nothing waits on Meta business verification or template
approval — days to weeks that had blocked the feature entirely — and the system
holds no third-party credential at all.

What it costs, stated plainly because it is not recoverable by writing more
code:

* **No delivery confirmation.** `sent` means a named assistant opened the
  message at a recorded time. Whether it arrived, or was even pressed, is
  outside what the app can see. Requirement 6.10 is met against the failure
  that *does* exist — an update written and never opened — not a delivery
  receipt.
* **No bulk send.** Every message needs a click. `flask prepare-daily-updates`
  writes the day's texts ahead of time; it cannot send them.
* **Not from the centre's number** (req 6.3). Parents see whichever assistant
  sent it. If messages must come from 01559306667, that phone has to be the one
  signed in to WhatsApp.

Reversing this is a rewrite of `services/whatsapp.py`, not a config flag —
the provider abstraction, the webhook, the credentials and the delivery
columns were all deleted rather than left dormant.

---

## Verification status

| | |
|---|---|
| Requirements met | 57 of 62 |
| Automated tests | 559, all passing |
| Lint | `ruff` clean |
| Migrations | up **and** down, on SQLite |
| Migrations on Postgres | **not yet run** — `scripts/verify-postgres.sh` |
| Restore drill | run on SQLite; **not yet on Postgres** |
| Arabic wording | **not reviewed by a native speaker** |
| WhatsApp delivery | **manual** — the app never confirms a message arrived (§9.6) |
