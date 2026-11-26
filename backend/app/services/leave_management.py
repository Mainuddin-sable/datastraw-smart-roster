from datetime import timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import EmployeeLeave, Organization
from app.services.employees import _employee
from app.services.organization_structure import ResourceConflict, ResourceNotFound


ACTIVE_LEAVE_STATUSES = ("requested", "approved")


def list_leave_requests(
    session: Session, organization_id: int, status: str | None = None,
) -> list[EmployeeLeave]:
    if session.get(Organization, organization_id) is None:
        raise ResourceNotFound("Organization not found.")
    statement = select(EmployeeLeave).where(EmployeeLeave.organization_id == organization_id)
    if status is not None:
        statement = statement.where(EmployeeLeave.status == status)
    return list(session.scalars(statement.order_by(EmployeeLeave.starts_at, EmployeeLeave.id)))


def create_leave_request(session: Session, organization_id: int, values: dict) -> EmployeeLeave:
    employee = _employee(session, organization_id, values["employee_id"], lock=True)
    if not employee.is_active:
        raise ResourceConflict("Leave requests can only be created for active employees.")
    starts_at = values["starts_at"].astimezone(timezone.utc)
    ends_at = values["ends_at"].astimezone(timezone.utc)

    overlap = session.scalar(select(EmployeeLeave.id).where(
        EmployeeLeave.organization_id == organization_id,
        EmployeeLeave.employee_id == employee.id,
        EmployeeLeave.status.in_(ACTIVE_LEAVE_STATUSES),
        EmployeeLeave.starts_at < ends_at,
        EmployeeLeave.ends_at > starts_at,
    ).limit(1))
    if overlap is not None:
        raise ResourceConflict("The leave request overlaps an existing requested or approved leave.")

    leave = EmployeeLeave(
        organization_id=organization_id,
        pod_id=employee.pod_id,
        employee_id=employee.id,
        starts_at=starts_at,
        ends_at=ends_at,
        leave_type=values["leave_type"],
        reason=values["reason"],
        status="requested",
    )
    session.add(leave)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflict("The leave request conflicts with existing employee data.") from exc
    session.refresh(leave)
    return leave


def update_leave_status(
    session: Session,
    organization_id: int,
    leave_id: int,
    status: str,
) -> EmployeeLeave:
    is_sqlite = session.get_bind().dialect.name == "sqlite"
    if is_sqlite:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    row = session.scalar(select(EmployeeLeave).where(
        EmployeeLeave.id == leave_id,
        EmployeeLeave.organization_id == organization_id,
    ).with_for_update())
    if row is None:
        raise ResourceNotFound("Leave request not found in this organization.")

    _employee(session, organization_id, row.employee_id, lock=not is_sqlite)
    allowed = {
        "requested": {"approved", "rejected", "cancelled"},
        "approved": {"cancelled"},
    }
    if status not in allowed.get(row.status, set()):
        raise ResourceConflict(f"A leave request cannot transition from {row.status} to {status}.")

    row.status = status
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflict("The leave status conflicts with existing employee data.") from exc
    session.refresh(row)
    return row
