from datetime import date

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ProcessAssignment(Base):
    __tablename__ = "process_assignments"
    __table_args__ = (
        UniqueConstraint("id", "employee_id", "organization_id", "pod_id", name="uq_process_assignments_scope"),
        UniqueConstraint("employee_id", "process_id", "effective_from", name="uq_employee_process_effective_from"),
        Index("ix_process_assignments_process_effective", "process_id", "effective_from", "effective_to"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_process_assignment_dates"),
        ForeignKeyConstraint(
            ["employee_id", "organization_id", "pod_id"],
            ["employees.id", "employees.organization_id", "employees.pod_id"],
            ondelete="CASCADE", name="fk_process_assignments_employee_scope",
        ),
        ForeignKeyConstraint(
            ["process_id", "organization_id", "pod_id"],
            ["processes.id", "processes.organization_id", "processes.pod_id"],
            ondelete="CASCADE", name="fk_process_assignments_process_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    pod_id: Mapped[int] = mapped_column(nullable=False)
    employee_id: Mapped[int] = mapped_column(index=True)
    process_id: Mapped[int] = mapped_column(index=True)
    effective_from: Mapped[date] = mapped_column(nullable=False)
    effective_to: Mapped[date | None] = mapped_column(nullable=True)
    employee: Mapped["Employee"] = relationship(back_populates="process_assignments", foreign_keys=[employee_id])
    process: Mapped["Process"] = relationship(back_populates="employee_assignments", foreign_keys=[process_id])
