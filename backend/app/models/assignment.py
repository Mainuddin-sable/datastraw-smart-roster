from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("employee_id", "shift_id", name="uq_assignment_employee_shift"),
        ForeignKeyConstraint(
            ["employee_id", "organization_id"],
            ["employees.id", "employees.organization_id"],
            ondelete="CASCADE",
            name="fk_assignments_employee_tenant",
        ),
        ForeignKeyConstraint(
            ["shift_id", "organization_id"],
            ["shifts.id", "shifts.organization_id"],
            ondelete="CASCADE",
            name="fk_assignments_shift_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE", name="fk_assignments_organization"), index=True
    )
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="assigned", server_default="assigned")
    employee: Mapped["Employee"] = relationship(back_populates="assignments", foreign_keys=[employee_id])
    shift: Mapped["Shift"] = relationship(back_populates="assignments", foreign_keys=[shift_id])
