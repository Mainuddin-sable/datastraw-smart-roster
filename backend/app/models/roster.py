from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON,
    String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Roster(Base):
    __tablename__ = "rosters"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", "pod_id", name="uq_rosters_id_tenant_pod"),
        UniqueConstraint("pod_id", "period_start", "period_end", "version", name="uq_roster_pod_period_version"),
        CheckConstraint("period_end >= period_start", name="ck_roster_period"),
        CheckConstraint("version > 0", name="ck_roster_version"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_roster_status"),
        ForeignKeyConstraint(
            ["pod_id", "organization_id"], ["pods.id", "pods.organization_id"],
            ondelete="CASCADE", name="fk_rosters_pod_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    pod_id: Mapped[int] = mapped_column(nullable=False, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    pod: Mapped["Pod"] = relationship(back_populates="rosters", foreign_keys=[pod_id])
    entries: Mapped[list["RosterEntry"]] = relationship(back_populates="roster", cascade="all, delete-orphan")
    generation_runs: Mapped[list["GenerationRun"]] = relationship(back_populates="roster", foreign_keys="GenerationRun.roster_id")


class RosterEntry(Base):
    """One employee's status for one work date in a roster version."""

    __tablename__ = "roster_entries"
    __table_args__ = (
        UniqueConstraint("roster_id", "employee_id", "work_date", name="uq_roster_employee_work_date"),
        UniqueConstraint("id", "employee_id", "organization_id", "pod_id", name="uq_roster_entries_scope"),
        Index("ix_roster_entries_roster_work_date", "roster_id", "work_date"),
        CheckConstraint("status IN ('working', 'day_off', 'leave', 'comp_off', 'unassigned')", name="ck_roster_entry_status"),
        ForeignKeyConstraint(
            ["roster_id", "organization_id", "pod_id"],
            ["rosters.id", "rosters.organization_id", "rosters.pod_id"],
            ondelete="CASCADE", name="fk_roster_entries_roster_scope",
        ),
        ForeignKeyConstraint(
            ["employee_id", "organization_id", "pod_id"],
            ["employees.id", "employees.organization_id", "employees.pod_id"],
            ondelete="CASCADE", name="fk_roster_entries_employee_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    roster_id: Mapped[int] = mapped_column(index=True)
    employee_id: Mapped[int] = mapped_column(index=True)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    roster: Mapped[Roster] = relationship(back_populates="entries", foreign_keys=[roster_id])
    work_assignments: Mapped[list["RosterWorkAssignment"]] = relationship(back_populates="roster_entry", cascade="all, delete-orphan")


class RosterWorkAssignment(Base):
    """A work block; multiple rows may belong to the same daily roster entry."""

    __tablename__ = "roster_work_assignments"
    __table_args__ = (
        UniqueConstraint("roster_entry_id", "process_assignment_id", "starts_at", "ends_at", name="uq_roster_work_block"),
        CheckConstraint("ends_at > starts_at", name="ck_roster_work_assignment_interval"),
        ForeignKeyConstraint(
            ["roster_entry_id", "employee_id", "organization_id", "pod_id"],
            ["roster_entries.id", "roster_entries.employee_id", "roster_entries.organization_id", "roster_entries.pod_id"],
            ondelete="CASCADE", name="fk_roster_work_entry_employee_scope",
        ),
        ForeignKeyConstraint(
            ["process_assignment_id", "employee_id", "organization_id", "pod_id"],
            ["process_assignments.id", "process_assignments.employee_id", "process_assignments.organization_id", "process_assignments.pod_id"],
            ondelete="CASCADE", name="fk_roster_work_process_assignment_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    roster_entry_id: Mapped[int] = mapped_column(index=True)
    employee_id: Mapped[int] = mapped_column(index=True)
    process_assignment_id: Mapped[int] = mapped_column(index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    work_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    roster_entry: Mapped[RosterEntry] = relationship(back_populates="work_assignments", foreign_keys=[roster_entry_id])
    process_assignment: Mapped["ProcessAssignment"] = relationship(foreign_keys=[process_assignment_id])


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_generation_run_status"),
        ForeignKeyConstraint(
            ["roster_id", "organization_id", "pod_id"],
            ["rosters.id", "rosters.organization_id", "rosters.pod_id"],
            ondelete="RESTRICT", name="fk_generation_runs_roster_scope_process",
        ),
        ForeignKeyConstraint(
            ["pod_id", "organization_id"], ["pods.id", "pods.organization_id"],
            ondelete="CASCADE", name="fk_generation_runs_pod_tenant",
        ),
        ForeignKeyConstraint(
            ["process_id", "organization_id", "pod_id"],
            ["processes.id", "processes.organization_id", "processes.pod_id"],
            ondelete="CASCADE", name="fk_generation_runs_process_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False, index=True)
    pod_id: Mapped[int] = mapped_column(nullable=False, index=True)
    process_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    roster_id: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", server_default="queued")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    roster: Mapped[Roster | None] = relationship(back_populates="generation_runs", foreign_keys=[roster_id])
    pod: Mapped["Pod"] = relationship(back_populates="generation_runs", foreign_keys=[pod_id])
