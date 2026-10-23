"""Opt-in semantic retrieval: task switch and chunk vector cache

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-23 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # false = keyword ranking only, which is what every existing task keeps.
    op.add_column('task', sa.Column('semantic_retrieval', sa.Boolean(), nullable=False, server_default='false'))
    op.create_table(
        'attachment_chunk_vec',
        sa.Column('attachment_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('task_attachment.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('ordinal', sa.Integer(), primary_key=True),
        sa.Column('content_hash', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('vec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('attachment_chunk_vec')
    op.drop_column('task', 'semantic_retrieval')
