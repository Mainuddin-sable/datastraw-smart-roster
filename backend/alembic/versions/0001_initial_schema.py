"""Initial roster entities.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_employees_organization_id", "employees", ["organization_id"])
    op.create_table(
        "shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shifts_organization_id", "shifts", ["organization_id"])
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="assigned", nullable=False),
        sa.UniqueConstraint("employee_id", "shift_id", name="uq_assignment_employee_shift"),
    )
    op.create_index("ix_assignments_employee_id", "assignments", ["employee_id"])
    op.create_index("ix_assignments_shift_id", "assignments", ["shift_id"])


def downgrade() -> None:
    op.drop_index("ix_assignments_shift_id", table_name="assignments")
    op.drop_index("ix_assignments_employee_id", table_name="assignments")
    op.drop_table("assignments")
    op.drop_index("ix_shifts_organization_id", table_name="shifts")
    op.drop_table("shifts")
    op.drop_index("ix_employees_organization_id", table_name="employees")
    op.drop_table("employees")
    op.drop_table("organizations")
