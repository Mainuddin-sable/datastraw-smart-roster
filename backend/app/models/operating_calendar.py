from datetime import date, time

from sqlalchemy import (
    Boolean, CheckConstraint, Date, ForeignKey, ForeignKeyConstraint, Index, Integer, String,
    Time, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OperatingRule(Base):
    """Recurring process hours; independent of individual employee availability."""

    __tablename__ = "operating_rules"
    __table_args__ = (
        UniqueConstraint("process_id", "day_of_week", "sequence", "effective_from", name="uq_operating_rule_period"),
        Index("ix_operating_rules_effective", "process_id", "day_of_week", "effective_from", "effective_to"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_operating_rule_weekday"),
        CheckConstraint("sequence > 0", name="ck_operating_rule_sequence"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_operating_rule_dates"),
        CheckConstraint(
            "(is_open AND opens_at IS NOT NULL AND closes_at IS NOT NULL AND opens_at != closes_at) "
            "OR (NOT is_open AND opens_at IS NULL AND closes_at IS NULL)",
            name="ck_operating_rule_hours",
        ),
        ForeignKeyConstraint(
            ["process_id", "organization_id", "pod_id"],
            ["processes.id", "processes.organization_id", "processes.pod_id"],
            ondelete="CASCADE", name="fk_operating_rules_process_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    process_id: Mapped[int] = mapped_column(index=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    process: Mapped["Process"] = relationship(back_populates="operating_rules", foreign_keys=[process_id])


class DayOverride(Base):
    """Recurring weekday exception over a process operating rule."""

    __tablename__ = "day_overrides"
    __table_args__ = (
        UniqueConstraint("process_id", "day_of_week", "sequence", "effective_from", name="uq_day_override_period"),
        Index("ix_day_overrides_effective", "process_id", "day_of_week", "effective_from", "effective_to"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_day_override_weekday"),
        CheckConstraint("sequence > 0", name="ck_day_override_sequence"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_day_override_dates"),
        CheckConstraint(
            "(is_open AND opens_at IS NOT NULL AND closes_at IS NOT NULL AND opens_at != closes_at) "
            "OR (NOT is_open AND opens_at IS NULL AND closes_at IS NULL)",
            name="ck_day_override_hours",
        ),
        ForeignKeyConstraint(
            ["process_id", "organization_id", "pod_id"],
            ["processes.id", "processes.organization_id", "processes.pod_id"],
            ondelete="CASCADE", name="fk_day_overrides_process_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    process_id: Mapped[int] = mapped_column(index=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    process: Mapped["Process"] = relationship(back_populates="day_overrides", foreign_keys=[process_id])


class DateOverride(Base):
    """One-off calendar-date exception for a process."""

    __tablename__ = "date_overrides"
    __table_args__ = (
        UniqueConstraint("process_id", "override_date", "sequence", name="uq_date_override_sequence"),
        Index("ix_date_overrides_process_date", "process_id", "override_date"),
        CheckConstraint("sequence > 0", name="ck_date_override_sequence"),
        CheckConstraint(
            "(is_open AND opens_at IS NOT NULL AND closes_at IS NOT NULL AND opens_at != closes_at) "
            "OR (NOT is_open AND opens_at IS NULL AND closes_at IS NULL)",
            name="ck_date_override_hours",
        ),
        ForeignKeyConstraint(
            ["process_id", "organization_id", "pod_id"],
            ["processes.id", "processes.organization_id", "processes.pod_id"],
            ondelete="CASCADE", name="fk_date_overrides_process_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    process_id: Mapped[int] = mapped_column(index=True)
    override_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    process: Mapped["Process"] = relationship(back_populates="date_overrides", foreign_keys=[process_id])


class Holiday(Base):
    __tablename__ = "holidays"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_holidays_id_organization"),
        UniqueConstraint("organization_id", "holiday_date", "name", name="uq_holiday_org_date_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    organization: Mapped["Organization"] = relationship(back_populates="holidays")
    overrides: Mapped[list["HolidayOverride"]] = relationship(back_populates="holiday", cascade="all, delete-orphan")


class HolidayOverride(Base):
    __tablename__ = "holiday_overrides"
    __table_args__ = (
        UniqueConstraint("holiday_id", "process_id", "sequence", name="uq_holiday_override_process"),
        CheckConstraint("sequence > 0", name="ck_holiday_override_sequence"),
        CheckConstraint(
            "(is_open AND opens_at IS NOT NULL AND closes_at IS NOT NULL AND opens_at != closes_at) "
            "OR (NOT is_open AND opens_at IS NULL AND closes_at IS NULL)",
            name="ck_holiday_override_hours",
        ),
        ForeignKeyConstraint(
            ["holiday_id", "organization_id"], ["holidays.id", "holidays.organization_id"],
            ondelete="CASCADE", name="fk_holiday_overrides_holiday_org",
        ),
        ForeignKeyConstraint(
            ["process_id", "organization_id", "pod_id"],
            ["processes.id", "processes.organization_id", "processes.pod_id"],
            ondelete="CASCADE", name="fk_holiday_overrides_process_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    holiday_id: Mapped[int] = mapped_column(index=True)
    process_id: Mapped[int] = mapped_column(index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    holiday: Mapped[Holiday] = relationship(back_populates="overrides", foreign_keys=[holiday_id])
    process: Mapped["Process"] = relationship(back_populates="holiday_overrides", foreign_keys=[process_id])
