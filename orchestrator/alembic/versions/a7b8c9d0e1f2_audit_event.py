"""audit_event: append-only, hash-chained record of who did what

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-21 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'audit_event',
        sa.Column('seq', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('actor', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('subject_type', sa.String(), nullable=True),
        sa.Column('subject_id', sa.String(), nullable=True),
        sa.Column('detail', postgresql.JSONB(), nullable=False),
        sa.Column('prev_hash', sa.String(), nullable=False),
        sa.Column('hash', sa.String(), nullable=False),
    )
    op.create_index('ix_audit_event_session_id', 'audit_event', ['session_id'])
    op.create_index('ix_audit_event_task_id', 'audit_event', ['task_id'])
    op.create_index('ix_audit_event_action', 'audit_event', ['action'])
    # The log is append-only: the application role cannot change or remove a row, nor empty the table.
    op.execute("""
        CREATE FUNCTION audit_event_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER audit_event_no_change BEFORE UPDATE OR DELETE ON audit_event "
               "FOR EACH ROW EXECUTE FUNCTION audit_event_immutable()")
    op.execute("CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event "
               "FOR EACH STATEMENT EXECUTE FUNCTION audit_event_immutable()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event")
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_change ON audit_event")
    op.drop_index('ix_audit_event_action', table_name='audit_event')
    op.drop_index('ix_audit_event_task_id', table_name='audit_event')
    op.drop_index('ix_audit_event_session_id', table_name='audit_event')
    op.drop_table('audit_event')
    op.execute("DROP FUNCTION IF EXISTS audit_event_immutable()")
