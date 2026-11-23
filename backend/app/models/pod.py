from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Pod(Base):
    __tablename__ = "pods"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_pods_id_organization"),
        UniqueConstraint("organization_id", "name", name="uq_pods_organization_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    organization: Mapped["Organization"] = relationship(back_populates="pods")
    brands: Mapped[list["Brand"]] = relationship(back_populates="pod", cascade="all, delete-orphan", foreign_keys="Brand.pod_id")
    employees: Mapped[list["Employee"]] = relationship(back_populates="pod", foreign_keys="Employee.pod_id")
    processes: Mapped[list["Process"]] = relationship(back_populates="pod", foreign_keys="Process.pod_id")
    rosters: Mapped[list["Roster"]] = relationship(back_populates="pod", foreign_keys="Roster.pod_id")
    generation_runs: Mapped[list["GenerationRun"]] = relationship(back_populates="pod", foreign_keys="GenerationRun.pod_id")
