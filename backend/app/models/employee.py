from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_employees_id_organization"),
        UniqueConstraint("id", "organization_id", "pod_id", name="uq_employees_id_tenant_pod"),
        UniqueConstraint("organization_id", "email", name="uq_employees_organization_email"),
        ForeignKeyConstraint(
            ["pod_id", "organization_id"],
            ["pods.id", "pods.organization_id"],
            ondelete="CASCADE",
            name="fk_employees_pod_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    pod_id: Mapped[int] = mapped_column(index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    organization: Mapped["Organization"] = relationship(back_populates="employees")
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan", foreign_keys="Assignment.employee_id"
    )
    pod: Mapped["Pod"] = relationship(back_populates="employees", foreign_keys=[pod_id])
    process_assignments: Mapped[list["ProcessAssignment"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan", foreign_keys="ProcessAssignment.employee_id"
    )
    availability: Mapped[list["EmployeeAvailability"]] = relationship(cascade="all, delete-orphan", foreign_keys="EmployeeAvailability.employee_id")
    leaves: Mapped[list["EmployeeLeave"]] = relationship(cascade="all, delete-orphan", foreign_keys="EmployeeLeave.employee_id")
    comp_offs: Mapped[list["EmployeeCompOff"]] = relationship(cascade="all, delete-orphan", foreign_keys="EmployeeCompOff.employee_id")
    preferences: Mapped[list["EmployeePreference"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan", foreign_keys="EmployeePreference.employee_id"
    )
    roster_entries: Mapped[list["RosterEntry"]] = relationship(foreign_keys="RosterEntry.employee_id")
