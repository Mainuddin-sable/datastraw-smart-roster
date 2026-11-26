from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PodLead(Base):
    __tablename__ = "pod_leads"
    __table_args__ = (
        UniqueConstraint("employee_id", name="uq_pod_leads_employee"),
        ForeignKeyConstraint(["pod_id", "organization_id"], ["pods.id", "pods.organization_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["employee_id", "organization_id", "pod_id"],
            ["employees.id", "employees.organization_id", "employees.pod_id"],
            ondelete="CASCADE",
        ),
    )

    pod_id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(nullable=False)
    employee_id: Mapped[int] = mapped_column(nullable=False)
    pod: Mapped["Pod"] = relationship(back_populates="lead", foreign_keys=[pod_id, organization_id])
