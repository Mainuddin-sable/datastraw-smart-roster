from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Brand, Employee, EmployeeAvailability, Organization, Pod, Process, ProcessAssignment
from app.services.organization_structure import DuplicateResource, ResourceConflict, ResourceNotFound


class InvalidRelationship(Exception):
    pass


def _commit(session: Session, resource):
    session.add(resource)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflict("The change conflicts with existing related data.") from exc
    session.refresh(resource)
    return resource


def _organization_exists(session: Session, organization_id: int) -> None:
    if session.get(Organization, organization_id) is None:
        raise ResourceNotFound("Organization not found.")


def _employee(session: Session, organization_id: int, employee_id: int, *, lock: bool = False) -> Employee:
    if lock and session.get_bind().dialect.name == "sqlite":
        # SQLite ignores SELECT FOR UPDATE. Acquire its write lock before the
        # overlap query so concurrent writers for different sessions cannot
        # both validate against the same stale availability/eligibility set.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    statement = select(Employee).where(
        Employee.id == employee_id, Employee.organization_id == organization_id
    )
    if lock:
        statement = statement.with_for_update()
    employee = session.scalar(statement)
    if employee is None:
        raise ResourceNotFound("Employee not found in this organization.")
    return employee


def _check_no_overlap(
    session: Session,
    model,
    *,
    employee_id: int,
    effective_from: date,
    effective_to: date | None,
    extra_filters: tuple = (),
    exclude_id: int | None = None,
) -> None:
    conditions = [model.employee_id == employee_id, *extra_filters]
    if exclude_id is not None:
        conditions.append(model.id != exclude_id)
    conditions.append(or_(model.effective_to.is_(None), model.effective_to >= effective_from))
    if effective_to is not None:
        conditions.append(model.effective_from <= effective_to)
    if session.scalar(select(model.id).where(*conditions).limit(1)) is not None:
        raise DuplicateResource("The effective date range overlaps an existing record.")


def _process_for_employee(session: Session, organization_id: int, employee: Employee, process_id: int) -> Process:
    process = session.scalar(
        select(Process).where(Process.id == process_id, Process.organization_id == organization_id)
    )
    if process is None:
        raise ResourceNotFound("Process not found in this organization.")
    if process.pod_id != employee.pod_id:
        raise InvalidRelationship("The process must belong to the employee's pod.")
    return process


def list_employees(session: Session, organization_id: int, is_active: bool | None = None) -> list[Employee]:
    _organization_exists(session, organization_id)
    statement = select(Employee).where(Employee.organization_id == organization_id)
    if is_active is not None:
        statement = statement.where(Employee.is_active == is_active)
    return list(session.scalars(statement.order_by(Employee.id)))


def get_employee(session: Session, organization_id: int, employee_id: int) -> Employee:
    return _employee(session, organization_id, employee_id)


def create_employee(
    session: Session,
    organization_id: int,
    pod_id: int,
    full_name: str,
    email: str,
    role: str | None,
    is_active: bool,
) -> Employee:
    _organization_exists(session, organization_id)
    pod = session.scalar(
        select(Pod).where(Pod.id == pod_id, Pod.organization_id == organization_id)
    )
    if pod is None:
        raise ResourceNotFound("Pod not found in this organization.")
    normalized_email = email.strip().lower()
    duplicate = session.scalar(
        select(Employee.id).where(
            Employee.organization_id == organization_id,
            func.lower(Employee.email) == normalized_email,
        )
    )
    if duplicate is not None:
        raise DuplicateResource("An employee with this email already exists in the organization.")
    return _commit(
        session,
        Employee(
            organization_id=organization_id,
            pod_id=pod_id,
            full_name=full_name.strip(),
            email=normalized_email,
            role=role.strip() if role else None,
            is_active=is_active,
        ),
    )


def update_employee(
    session: Session,
    organization_id: int,
    employee_id: int,
    full_name: str,
    email: str,
    role: str | None,
    is_active: bool,
) -> Employee:
    employee = _employee(session, organization_id, employee_id, lock=True)
    normalized_email = email.strip().lower()
    duplicate = session.scalar(
        select(Employee.id).where(
            Employee.organization_id == organization_id,
            func.lower(Employee.email) == normalized_email,
            Employee.id != employee_id,
        )
    )
    if duplicate is not None:
        raise DuplicateResource("An employee with this email already exists in the organization.")
    employee.full_name = full_name.strip()
    employee.email = normalized_email
    employee.role = role.strip() if role else None
    employee.is_active = is_active
    return _commit(session, employee)


def list_process_assignments(session: Session, organization_id: int, employee_id: int) -> list[dict]:
    _employee(session, organization_id, employee_id)
    statement = (
        select(ProcessAssignment, Process, Brand)
        .join(Process, Process.id == ProcessAssignment.process_id)
        .join(Brand, Brand.id == Process.brand_id)
        .where(
            ProcessAssignment.organization_id == organization_id,
            ProcessAssignment.employee_id == employee_id,
        )
        .order_by(ProcessAssignment.effective_from, ProcessAssignment.id)
    )
    return [
        {
            "id": assignment.id,
            "employee_id": assignment.employee_id,
            "process_id": process.id,
            "process_name": process.name,
            "brand_id": brand.id,
            "brand_name": brand.name,
            "effective_from": assignment.effective_from,
            "effective_to": assignment.effective_to,
        }
        for assignment, process, brand in session.execute(statement)
    ]


def create_process_assignment(
    session: Session,
    organization_id: int,
    employee_id: int,
    process_id: int,
    effective_from: date,
    effective_to: date | None,
) -> ProcessAssignment:
    employee = _employee(session, organization_id, employee_id, lock=True)
    process = _process_for_employee(session, organization_id, employee, process_id)
    _check_no_overlap(
        session,
        ProcessAssignment,
        employee_id=employee_id,
        effective_from=effective_from,
        effective_to=effective_to,
        extra_filters=(ProcessAssignment.process_id == process.id,),
    )
    return _commit(
        session,
        ProcessAssignment(
            organization_id=organization_id,
            pod_id=employee.pod_id,
            employee_id=employee_id,
            process_id=process.id,
            effective_from=effective_from,
            effective_to=effective_to,
        ),
    )


def update_process_assignment(
    session: Session,
    organization_id: int,
    employee_id: int,
    assignment_id: int,
    process_id: int,
    effective_from: date,
    effective_to: date | None,
) -> ProcessAssignment:
    employee = _employee(session, organization_id, employee_id, lock=True)
    assignment = session.scalar(
        select(ProcessAssignment).where(
            ProcessAssignment.id == assignment_id,
            ProcessAssignment.organization_id == organization_id,
            ProcessAssignment.employee_id == employee_id,
        )
    )
    if assignment is None:
        raise ResourceNotFound("Process assignment not found for this employee.")
    process = _process_for_employee(session, organization_id, employee, process_id)
    _check_no_overlap(
        session,
        ProcessAssignment,
        employee_id=employee_id,
        effective_from=effective_from,
        effective_to=effective_to,
        extra_filters=(ProcessAssignment.process_id == process.id,),
        exclude_id=assignment_id,
    )
    assignment.process_id = process.id
    assignment.effective_from = effective_from
    assignment.effective_to = effective_to
    return _commit(session, assignment)


def deactivate_process_assignment(
    session: Session, organization_id: int, employee_id: int, assignment_id: int, effective_to: date
) -> ProcessAssignment:
    _employee(session, organization_id, employee_id, lock=True)
    assignment = session.scalar(
        select(ProcessAssignment).where(
            ProcessAssignment.id == assignment_id,
            ProcessAssignment.organization_id == organization_id,
            ProcessAssignment.employee_id == employee_id,
        )
    )
    if assignment is None:
        raise ResourceNotFound("Process assignment not found for this employee.")
    if effective_to < assignment.effective_from:
        raise InvalidRelationship("effective_to must be on or after effective_from.")
    _check_no_overlap(
        session,
        ProcessAssignment,
        employee_id=employee_id,
        effective_from=assignment.effective_from,
        effective_to=effective_to,
        extra_filters=(ProcessAssignment.process_id == assignment.process_id,),
        exclude_id=assignment_id,
    )
    assignment.effective_to = effective_to
    return _commit(session, assignment)


def list_availability(
    session: Session,
    organization_id: int,
    employee_id: int,
    start_date: date,
    end_date: date,
) -> list[EmployeeAvailability]:
    _employee(session, organization_id, employee_id)
    statement = select(EmployeeAvailability).where(
        EmployeeAvailability.organization_id == organization_id,
        EmployeeAvailability.employee_id == employee_id,
        EmployeeAvailability.effective_from <= end_date,
        or_(EmployeeAvailability.effective_to.is_(None), EmployeeAvailability.effective_to >= start_date),
    ).order_by(EmployeeAvailability.day_of_week, EmployeeAvailability.sequence, EmployeeAvailability.effective_from)
    return list(session.scalars(statement))


def create_availability(
    session: Session, organization_id: int, employee_id: int, values: dict
) -> EmployeeAvailability:
    employee = _employee(session, organization_id, employee_id, lock=True)
    _check_no_overlap(
        session,
        EmployeeAvailability,
        employee_id=employee_id,
        effective_from=values["effective_from"],
        effective_to=values["effective_to"],
        extra_filters=(
            EmployeeAvailability.day_of_week == values["day_of_week"],
            EmployeeAvailability.sequence == values["sequence"],
        ),
    )
    return _commit(
        session,
        EmployeeAvailability(
            organization_id=organization_id,
            pod_id=employee.pod_id,
            employee_id=employee_id,
            **values,
        ),
    )


def update_availability(
    session: Session,
    organization_id: int,
    employee_id: int,
    availability_id: int,
    values: dict,
) -> EmployeeAvailability:
    employee = _employee(session, organization_id, employee_id, lock=True)
    availability = session.scalar(
        select(EmployeeAvailability).where(
            EmployeeAvailability.id == availability_id,
            EmployeeAvailability.organization_id == organization_id,
            EmployeeAvailability.employee_id == employee_id,
        )
    )
    if availability is None:
        raise ResourceNotFound("Availability record not found for this employee.")
    _check_no_overlap(
        session,
        EmployeeAvailability,
        employee_id=employee_id,
        effective_from=values["effective_from"],
        effective_to=values["effective_to"],
        extra_filters=(
            EmployeeAvailability.day_of_week == values["day_of_week"],
            EmployeeAvailability.sequence == values["sequence"],
        ),
        exclude_id=availability_id,
    )
    for key, value in values.items():
        setattr(availability, key, value)
    availability.pod_id = employee.pod_id
    return _commit(session, availability)
