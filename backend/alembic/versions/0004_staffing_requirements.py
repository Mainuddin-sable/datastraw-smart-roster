"""add effective-dated minimum staffing requirements

Revision ID: 0004_staffing_requirements
Revises: 0003_employee_active_status
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_staffing_requirements"
down_revision = "0003_employee_active_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_holiday_dates = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM (SELECT organization_id, holiday_date FROM holidays "
        "GROUP BY organization_id, holiday_date HAVING COUNT(*) > 1) AS duplicate_holiday_dates"
    ).scalar_one()
    if duplicate_holiday_dates:
        raise RuntimeError(
            "Cannot enforce one holiday per organization and date: duplicate dates already exist."
        )

    with op.batch_alter_table("holidays") as batch_op:
        batch_op.drop_constraint("uq_holiday_org_date_name", type_="unique")
        batch_op.create_unique_constraint("uq_holiday_org_date", ["organization_id", "holiday_date"])

    with op.batch_alter_table("processes") as batch_op:
        batch_op.create_unique_constraint(
            "uq_processes_id_tenant_brand_pod",
            ["id", "organization_id", "pod_id", "brand_id"],
        )

    op.create_table(
        "staffing_requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("pod_id", sa.Integer(), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("override_date", sa.Date(), nullable=True),
        sa.Column("minimum_staff", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.CheckConstraint("minimum_staff >= 0", name="ck_staffing_requirement_nonnegative"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_staffing_requirement_dates",
        ),
        sa.CheckConstraint(
            "day_of_week IS NULL OR day_of_week BETWEEN 0 AND 6",
            name="ck_staffing_requirement_weekday",
        ),
        sa.CheckConstraint(
            "override_date IS NULL OR (day_of_week IS NULL AND effective_from = override_date AND effective_to = override_date)",
            name="ck_staffing_requirement_date_override",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "organization_id", "pod_id"],
            ["brands.id", "brands.organization_id", "brands.pod_id"],
            ondelete="CASCADE",
            name="fk_staffing_requirement_brand_scope",
        ),
        sa.ForeignKeyConstraint(
            ["process_id", "organization_id", "pod_id", "brand_id"],
            ["processes.id", "processes.organization_id", "processes.pod_id", "processes.brand_id"],
            ondelete="CASCADE",
            name="fk_staffing_requirement_process_brand_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", "pod_id", "brand_id", name="uq_staffing_requirement_scope"
        ),
    )
    op.create_index("ix_staffing_requirements_organization_id", "staffing_requirements", ["organization_id"])
    op.create_index("ix_staffing_requirements_pod_id", "staffing_requirements", ["pod_id"])
    op.create_index("ix_staffing_requirements_brand_id", "staffing_requirements", ["brand_id"])
    op.create_index("ix_staffing_requirements_process_id", "staffing_requirements", ["process_id"])
    op.create_index("ix_staffing_requirements_override_date", "staffing_requirements", ["override_date"])
    for name, columns, predicate in (
        ("uq_staffing_brand_default_period", ["organization_id", "brand_id", "effective_from"], "process_id IS NULL AND day_of_week IS NULL AND override_date IS NULL"),
        ("uq_staffing_process_default_period", ["organization_id", "brand_id", "process_id", "effective_from"], "process_id IS NOT NULL AND day_of_week IS NULL AND override_date IS NULL"),
        ("uq_staffing_brand_weekday_period", ["organization_id", "brand_id", "day_of_week", "effective_from"], "process_id IS NULL AND day_of_week IS NOT NULL AND override_date IS NULL"),
        ("uq_staffing_process_weekday_period", ["organization_id", "brand_id", "process_id", "day_of_week", "effective_from"], "process_id IS NOT NULL AND day_of_week IS NOT NULL AND override_date IS NULL"),
        ("uq_staffing_brand_date_override", ["organization_id", "brand_id", "override_date"], "process_id IS NULL AND override_date IS NOT NULL"),
        ("uq_staffing_process_date_override", ["organization_id", "brand_id", "process_id", "override_date"], "process_id IS NOT NULL AND override_date IS NOT NULL"),
    ):
        op.create_index(
            name,
            "staffing_requirements",
            columns,
            unique=True,
            sqlite_where=sa.text(predicate),
            postgresql_where=sa.text(predicate),
        )


def downgrade() -> None:
    for index_name in (
        "uq_staffing_process_date_override",
        "uq_staffing_brand_date_override",
        "uq_staffing_process_weekday_period",
        "uq_staffing_brand_weekday_period",
        "uq_staffing_process_default_period",
        "uq_staffing_brand_default_period",
    ):
        op.drop_index(index_name, table_name="staffing_requirements")
    op.drop_index("ix_staffing_requirements_override_date", table_name="staffing_requirements")
    op.drop_index("ix_staffing_requirements_process_id", table_name="staffing_requirements")
    op.drop_index("ix_staffing_requirements_brand_id", table_name="staffing_requirements")
    op.drop_index("ix_staffing_requirements_pod_id", table_name="staffing_requirements")
    op.drop_index("ix_staffing_requirements_organization_id", table_name="staffing_requirements")
    op.drop_table("staffing_requirements")
    with op.batch_alter_table("holidays") as batch_op:
        batch_op.drop_constraint("uq_holiday_org_date", type_="unique")
        batch_op.create_unique_constraint(
            "uq_holiday_org_date_name", ["organization_id", "holiday_date", "name"]
        )
    with op.batch_alter_table("processes") as batch_op:
        batch_op.drop_constraint("uq_processes_id_tenant_brand_pod", type_="unique")
