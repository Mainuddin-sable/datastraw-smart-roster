"""add active status to employees

Revision ID: 0003_employee_active_status
Revises: 0002_complete_roster_domain
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_employee_active_status"
down_revision = "0002_complete_roster_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("employees", "is_active")
