from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (UniqueConstraint("id", "organization_id", name="uq_shifts_id_organization"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    organization: Mapped["Organization"] = relationship(back_populates="shifts")
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="shift", cascade="all, delete-orphan", foreign_keys="Assignment.shift_id"
    )
