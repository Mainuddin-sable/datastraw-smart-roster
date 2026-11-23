from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Process(Base):
    __tablename__ = "processes"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", "pod_id", name="uq_processes_id_tenant_pod"),
        UniqueConstraint("id", "organization_id", "pod_id", "brand_id", name="uq_processes_id_tenant_brand_pod"),
        UniqueConstraint("organization_id", "brand_id", "name", name="uq_processes_brand_name"),
        ForeignKeyConstraint(
            ["pod_id", "organization_id"], ["pods.id", "pods.organization_id"],
            ondelete="CASCADE", name="fk_processes_pod_tenant",
        ),
        ForeignKeyConstraint(
            ["brand_id", "pod_id", "organization_id"],
            ["brands.id", "brands.pod_id", "brands.organization_id"],
            ondelete="CASCADE", name="fk_processes_brand_tenant_pod",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    pod_id: Mapped[int] = mapped_column(nullable=False, index=True)
    brand_id: Mapped[int] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    pod: Mapped["Pod"] = relationship(back_populates="processes", foreign_keys=[pod_id])
    brand: Mapped["Brand"] = relationship(back_populates="processes", foreign_keys=[brand_id])
    employee_assignments: Mapped[list["ProcessAssignment"]] = relationship(back_populates="process", cascade="all, delete-orphan", foreign_keys="ProcessAssignment.process_id")
    operating_rules: Mapped[list["OperatingRule"]] = relationship(back_populates="process", cascade="all, delete-orphan", foreign_keys="OperatingRule.process_id")
    day_overrides: Mapped[list["DayOverride"]] = relationship(back_populates="process", cascade="all, delete-orphan", foreign_keys="DayOverride.process_id")
    date_overrides: Mapped[list["DateOverride"]] = relationship(back_populates="process", cascade="all, delete-orphan", foreign_keys="DateOverride.process_id")
    holiday_overrides: Mapped[list["HolidayOverride"]] = relationship(back_populates="process", cascade="all, delete-orphan", foreign_keys="HolidayOverride.process_id")
    staffing_requirements: Mapped[list["StaffingRequirement"]] = relationship(back_populates="process", foreign_keys="StaffingRequirement.process_id")
