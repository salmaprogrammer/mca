"""WhatsApp message log (sprint S6.2).

Every daily update the centre composes is recorded here, whether or not an
assistant has sent it yet. Nothing is deleted: a message a family disputes
later is exactly the row worth keeping.

There is **no API integration**. The app writes the text and builds a
click-to-chat link; a named assistant opens it in their own WhatsApp and
presses send. So the only two facts this table can honestly hold are "composed"
and "opened in WhatsApp by <person> at <time>" — there is no provider to report
delivery, and inventing a `delivered` column would be a guess dressed as a
record.

`batch_date` is the idempotency key for the daily run. A row carrying a
`batch_date` *is* the daily message for that student on that date, and the
unique constraint stops a second preparation from duplicating a family's
update. A deliberate re-send is stored with `batch_date = NULL`, which the
constraint ignores — it is an extra message, not a second "daily update".
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UtcDateTime
from app.models.enums import MessageStatus, enum_column


class WhatsAppMessage(TimestampMixin, db.Model):
    __tablename__ = "whatsapp_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("users.id"), nullable=False, index=True
    )
    # Who it goes to. Nullable because a student may have no linked parent yet.
    recipient_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"), index=True)
    to_phone: Mapped[str] = mapped_column(sa.String(24), nullable=False)

    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    locale: Mapped[str] = mapped_column(sa.String(5), default="ar", nullable=False)

    status: Mapped[MessageStatus] = mapped_column(
        enum_column(MessageStatus, 12),
        default=MessageStatus.PREPARED,
        nullable=False,
        index=True,
    )

    # NULL for a deliberate re-send; set for the once-a-day update.
    batch_date: Mapped[date | None] = mapped_column(sa.Date, index=True)
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    # Who opened it in WhatsApp. NULL while the message is only prepared.
    sent_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id"))
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    student = relationship("User", foreign_keys=[student_id])
    recipient = relationship("User", foreign_keys=[recipient_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    sent_by = relationship("User", foreign_keys=[sent_by_id])

    __table_args__ = (
        sa.UniqueConstraint("student_id", "batch_date", name="uq_whatsapp_daily_once"),
    )

    @property
    def actually_left_the_building(self) -> bool:
        """Whether a person has taken this message to WhatsApp.

        Even SENT is not proof a parent received it — the app hands off to
        WhatsApp and cannot watch what happens next. It *is* proof that a named
        assistant opened it at a recorded time, which is what staff need when a
        parent says they were never told.
        """
        return self.status is MessageStatus.SENT

    def __repr__(self) -> str:
        return f"<WhatsAppMessage {self.id} student={self.student_id} {self.status.value}>"
