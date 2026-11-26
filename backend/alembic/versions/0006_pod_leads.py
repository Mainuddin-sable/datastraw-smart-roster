"""associate an employee as lead for each Pod

Revision ID: 0006_pod_leads
Revises: 0005_roster_assignment_locks
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_pod_leads"
down_revision = "0005_roster_assignment_locks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pod_leads",
        sa.Column("pod_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["pod_id", "organization_id"], ["pods.id", "pods.organization_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["employee_id", "organization_id", "pod_id"],
            ["employees.id", "employees.organization_id", "employees.pod_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("pod_id"),
        sa.UniqueConstraint("employee_id", name="uq_pod_leads_employee"),
    )


def downgrade() -> None:
    op.drop_table("pod_leads")
