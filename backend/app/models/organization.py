from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    pods: Mapped[list["Pod"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    employees: Mapped[list["Employee"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    shifts: Mapped[list["Shift"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    holidays: Mapped[list["Holiday"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="organization")
