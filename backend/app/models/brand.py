from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", "pod_id", name="uq_brands_id_tenant_pod"),
        UniqueConstraint("organization_id", "pod_id", "name", name="uq_brands_pod_name"),
        ForeignKeyConstraint(
            ["pod_id", "organization_id"], ["pods.id", "pods.organization_id"],
            ondelete="CASCADE", name="fk_brands_pod_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    pod_id: Mapped[int] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    pod: Mapped["Pod"] = relationship(back_populates="brands", foreign_keys=[pod_id])
    processes: Mapped[list["Process"]] = relationship(back_populates="brand", cascade="all, delete-orphan", foreign_keys="Process.brand_id")
    staffing_requirements: Mapped[list["StaffingRequirement"]] = relationship(back_populates="brand", foreign_keys="StaffingRequirement.brand_id")
