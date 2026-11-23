"""persist manual roster assignment locks

Revision ID: 0005_roster_assignment_locks
Revises: 0004_staffing_requirements
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_roster_assignment_locks"
down_revision = "0004_staffing_requirements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roster_work_assignments") as batch_op:
        batch_op.add_column(sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("roster_work_assignments") as batch_op:
        batch_op.drop_column("is_locked")
