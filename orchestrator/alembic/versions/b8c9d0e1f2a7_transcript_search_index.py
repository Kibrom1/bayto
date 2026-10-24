"""Full-text search over transcripts: expression GIN indexes (no table change)

Revision ID: b8c9d0e1f2a7
Revises: d0e1f2a3b4c5
Create Date: 2026-10-23 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a7'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expression indexes: the query in api/search.py repeats these expressions exactly so the planner can use them.
    op.execute("CREATE INDEX ix_message_body_fts ON message USING gin (to_tsvector('english', body))")
    op.execute("CREATE INDEX ix_artifact_content_fts ON artifact USING gin (to_tsvector('english', content_json))")


def downgrade() -> None:
    op.execute("DROP INDEX ix_artifact_content_fts")
    op.execute("DROP INDEX ix_message_body_fts")
