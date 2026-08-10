"""P5: enrollment amount_due and paid_at

Hand-adjusted from autogenerate. `amount_due` is NOT NULL, and a table with
existing rows cannot take a NOT NULL column in one step — so it is added
nullable, backfilled, then tightened.

The backfill takes each course's current price rather than zero: an enrolment
created before this column existed was still for a course with a price, and
defaulting everyone to 0 would silently declare every existing family paid up.

Revision ID: 30b9b373298c
Revises: e979e387596c
Create Date: 2026-08-08 00:58:31.933704

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '30b9b373298c'
down_revision = 'e979e387596c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('enrollments', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('amount_due', sa.Numeric(precision=10, scale=2), nullable=True)
        )
        batch_op.add_column(sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True))

    op.execute(
        """
        UPDATE enrollments
           SET amount_due = COALESCE(
                   (SELECT price_egp FROM courses WHERE courses.id = enrollments.course_id),
                   0
               )
         WHERE amount_due IS NULL
        """
    )

    with op.batch_alter_table('enrollments', schema=None) as batch_op:
        batch_op.alter_column(
            'amount_due',
            existing_type=sa.Numeric(precision=10, scale=2),
            nullable=False,
        )


def downgrade():
    with op.batch_alter_table('enrollments', schema=None) as batch_op:
        batch_op.drop_column('paid_at')
        batch_op.drop_column('amount_due')
