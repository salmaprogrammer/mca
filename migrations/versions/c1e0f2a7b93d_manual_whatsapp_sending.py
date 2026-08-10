"""Manual WhatsApp sending: drop the Cloud API columns

Revision ID: c1e0f2a7b93d
Revises: 8adf848519da
Create Date: 2026-08-09

Daily updates are now sent by hand from the assistant's own WhatsApp, so every
column that only a provider could fill has nothing left to write it: the
provider name and message id, the error code and detail, the template name, the
centre's sender number, and the delivered/read timestamps that came from the
webhook. Kept, they would read as evidence and hold none.

`status` loses `delivered`, `read` and `failed` for the same reason — nobody can
observe any of the three. It is a plain VARCHAR on both backends (`enum_column`
sets `create_constraint=False`), so the vocabulary change is a data update, not
a constraint rebuild.

The mapping is deliberately pessimistic. `delivered` and `read` become `sent`
because a person did send them; `failed` becomes `prepared` because the family
never got it and somebody should.

**Downgrading restores the columns empty.** The provider ids and error text are
gone for good — that is the cost of the change, and it is why this migration
does not pretend to be reversible in anything but shape.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c1e0f2a7b93d'
down_revision = '8adf848519da'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE whatsapp_messages SET status = 'sent' "
        "WHERE status IN ('delivered', 'read')"
    )
    op.execute(
        "UPDATE whatsapp_messages SET status = 'prepared', sent_at = NULL "
        "WHERE status IN ('queued', 'failed')"
    )

    with op.batch_alter_table('whatsapp_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sent_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_whatsapp_messages_sent_by_id_users', 'users', ['sent_by_id'], ['id']
        )
        batch_op.drop_index(batch_op.f('ix_whatsapp_messages_provider_message_id'))
        batch_op.drop_column('provider_message_id')
        batch_op.drop_column('provider')
        batch_op.drop_column('error_code')
        batch_op.drop_column('error_detail')
        batch_op.drop_column('template_name')
        batch_op.drop_column('from_phone')
        batch_op.drop_column('delivered_at')
        batch_op.drop_column('read_at')


def downgrade():
    # from_phone is NOT NULL, so it needs a value for the existing rows before
    # the constraint goes back on: add it nullable, backfill, then tighten.
    with op.batch_alter_table('whatsapp_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('from_phone', sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column('template_name', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('provider', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('provider_message_id', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('error_code', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('error_detail', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE whatsapp_messages SET from_phone = '', provider = 'null'")
    op.execute("UPDATE whatsapp_messages SET status = 'queued' WHERE status = 'prepared'")

    with op.batch_alter_table('whatsapp_messages', schema=None) as batch_op:
        batch_op.alter_column(
            'from_phone', existing_type=sa.String(length=24), nullable=False
        )
        batch_op.alter_column(
            'provider', existing_type=sa.String(length=32), nullable=False
        )
        batch_op.create_index(
            batch_op.f('ix_whatsapp_messages_provider_message_id'),
            ['provider_message_id'],
            unique=False,
        )
        batch_op.drop_constraint('fk_whatsapp_messages_sent_by_id_users', type_='foreignkey')
        batch_op.drop_column('sent_by_id')
