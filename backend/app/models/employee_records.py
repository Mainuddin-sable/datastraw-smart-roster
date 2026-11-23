from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, JSON, Numeric, String, Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EmployeeAvailability(Base):
    """Employee-specific recurring availability, not a process operating calendar."""

    __tablename__ = "employee_availability"
    __table_args__ = (
        UniqueConstraint("employee_id", "day_of_week", "sequence", "effective_from", name="uq_employee_availability_period"),
        Index("ix_employee_availability_effective", "employee_id", "day_of_week", "effective_from", "effective_to"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_employee_availability_weekday"),
        CheckConstraint("sequence > 0", name="ck_employee_availability_sequence"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_employee_availability_dates"),
        CheckConstraint(
            "(is_available AND ((available_from IS NULL AND available_to IS NULL) "
            "OR (available_from IS NOT NULL AND available_to IS NOT NULL AND available_from != available_to))) "
            "OR (NOT is_available AND available_from IS NULL AND available_to IS NULL)",
            name="ck_employee_availability_hours",
        ),
        ForeignKeyConstraint(
            ["employee_id", "organization_id", "pod_id"],
            ["employees.id", "employees.organization_id", "employees.pod_id"],
            ondelete="CASCADE", name="fk_employee_availability_employee_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    employee_id: Mapped[int] = mapped_column(index=True)
    day_of_week: Mapped[int] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    is_available: Mapped[bool] = mapped_column(nullable=False, default=True)
    available_from: Mapped[time | None] = mapped_column(Time, nullable=True)
    available_to: Mapped[time | None] = mapped_column(Time, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class EmployeeLeave(Base):
    __tablename__ = "employee_leaves"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_employee_leave_interval"),
        CheckConstraint("status IN ('requested', 'approved', 'rejected', 'cancelled')", name="ck_employee_leave_status"),
        Index("ix_employee_leaves_employee_period", "employee_id", "starts_at", "ends_at"),
        ForeignKeyConstraint(
            ["employee_id", "organization_id", "pod_id"],
            ["employees.id", "employees.organization_id", "employees.pod_id"],
            ondelete="CASCADE", name="fk_employee_leaves_employee_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    employee_id: Mapped[int] = mapped_column(index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    leave_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="requested", server_default="requested")
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class EmployeeCompOff(Base):
    """Comp-off grant and its usage; each row preserves an auditable grant."""

    __tablename__ = "employee_comp_offs"
    __table_args__ = (
        CheckConstraint("hours_earned > 0", name="ck_comp_off_hours_earned_positive"),
        CheckConstraint("hours_used >= 0 AND hours_used <= hours_earned", name="ck_comp_off_hours_used_range"),
        CheckConstraint("expires_on IS NULL OR expires_on >= earned_on", name="ck_comp_off_expiry"),
        ForeignKeyConstraint(
            ["employee_id", "organization_id", "pod_id"],
            ["employees.id", "employees.organization_id", "employees.pod_id"],
            ondelete="CASCADE", name="fk_employee_comp_offs_employee_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    employee_id: Mapped[int] = mapped_column(index=True)
    earned_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    hours_earned: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    hours_used: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False, default=0, server_default="0")
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class EmployeePreference(Base):
    __tablename__ = "employee_preferences"
    __table_args__ = (
        UniqueConstraint("employee_id", "preference_key", "effective_from", name="uq_employee_preference_period"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_employee_preference_dates"),
        ForeignKeyConstraint(
            ["employee_id", "organization_id", "pod_id"],
            ["employees.id", "employees.organization_id", "employees.pod_id"],
            ondelete="CASCADE", name="fk_employee_preferences_employee_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    employee_id: Mapped[int] = mapped_column(index=True)
    preference_key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    employee: Mapped["Employee"] = relationship(back_populates="preferences", foreign_keys=[employee_id])
