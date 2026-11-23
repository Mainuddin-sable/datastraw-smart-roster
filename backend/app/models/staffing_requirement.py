from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class StaffingRequirement(Base):
    """Effective-dated minimum staffing for a Brand or one of its Processes."""

    __tablename__ = "staffing_requirements"
    __table_args__ = (
        UniqueConstraint(
            "id", "organization_id", "pod_id", "brand_id", name="uq_staffing_requirement_scope"
        ),
        Index(
            "uq_staffing_brand_default_period", "organization_id", "brand_id", "effective_from",
            unique=True,
            sqlite_where=text("process_id IS NULL AND day_of_week IS NULL AND override_date IS NULL"),
            postgresql_where=text("process_id IS NULL AND day_of_week IS NULL AND override_date IS NULL"),
        ),
        Index(
            "uq_staffing_process_default_period", "organization_id", "brand_id", "process_id", "effective_from",
            unique=True,
            sqlite_where=text("process_id IS NOT NULL AND day_of_week IS NULL AND override_date IS NULL"),
            postgresql_where=text("process_id IS NOT NULL AND day_of_week IS NULL AND override_date IS NULL"),
        ),
        Index(
            "uq_staffing_brand_weekday_period", "organization_id", "brand_id", "day_of_week", "effective_from",
            unique=True,
            sqlite_where=text("process_id IS NULL AND day_of_week IS NOT NULL AND override_date IS NULL"),
            postgresql_where=text("process_id IS NULL AND day_of_week IS NOT NULL AND override_date IS NULL"),
        ),
        Index(
            "uq_staffing_process_weekday_period", "organization_id", "brand_id", "process_id", "day_of_week", "effective_from",
            unique=True,
            sqlite_where=text("process_id IS NOT NULL AND day_of_week IS NOT NULL AND override_date IS NULL"),
            postgresql_where=text("process_id IS NOT NULL AND day_of_week IS NOT NULL AND override_date IS NULL"),
        ),
        Index(
            "uq_staffing_brand_date_override", "organization_id", "brand_id", "override_date",
            unique=True,
            sqlite_where=text("process_id IS NULL AND override_date IS NOT NULL"),
            postgresql_where=text("process_id IS NULL AND override_date IS NOT NULL"),
        ),
        Index(
            "uq_staffing_process_date_override", "organization_id", "brand_id", "process_id", "override_date",
            unique=True,
            sqlite_where=text("process_id IS NOT NULL AND override_date IS NOT NULL"),
            postgresql_where=text("process_id IS NOT NULL AND override_date IS NOT NULL"),
        ),
        CheckConstraint("minimum_staff >= 0", name="ck_staffing_requirement_nonnegative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_staffing_requirement_dates",
        ),
        CheckConstraint(
            "day_of_week IS NULL OR day_of_week BETWEEN 0 AND 6",
            name="ck_staffing_requirement_weekday",
        ),
        CheckConstraint(
            "override_date IS NULL OR (day_of_week IS NULL AND effective_from = override_date AND effective_to = override_date)",
            name="ck_staffing_requirement_date_override",
        ),
        ForeignKeyConstraint(
            ["brand_id", "organization_id", "pod_id"],
            ["brands.id", "brands.organization_id", "brands.pod_id"],
            ondelete="CASCADE",
            name="fk_staffing_requirement_brand_scope",
        ),
        ForeignKeyConstraint(
            ["process_id", "organization_id", "pod_id", "brand_id"],
            ["processes.id", "processes.organization_id", "processes.pod_id", "processes.brand_id"],
            ondelete="CASCADE",
            name="fk_staffing_requirement_process_brand_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    pod_id: Mapped[int] = mapped_column(nullable=False, index=True)
    brand_id: Mapped[int] = mapped_column(nullable=False, index=True)
    process_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    override_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    minimum_staff: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    brand: Mapped["Brand"] = relationship(back_populates="staffing_requirements", foreign_keys=[brand_id])
    process: Mapped["Process | None"] = relationship(back_populates="staffing_requirements", foreign_keys=[process_id])
