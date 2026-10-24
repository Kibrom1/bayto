"""Per-seat local fallback: task.local_fallback, session.fallback_state; drops session.fallback_active

Revision ID: e1f2a3b4c5d6
Revises: b8c9d0e1f2a7
Create Date: 2026-10-23 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'b8c9d0e1f2a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('task', sa.Column('local_fallback', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('session', sa.Column('fallback_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    # The old flag switched a whole session to Ollama for good. It has no per-seat meaning, so it is dropped, not
    # converted: a session that had it set simply asks Claude again on its next turn.
    op.drop_column('session', 'fallback_active')


def downgrade() -> None:
    op.add_column('session', sa.Column('fallback_active', sa.Boolean(), nullable=False, server_default='false'))
    op.drop_column('session', 'fallback_state')
    op.drop_column('task', 'local_fallback')
