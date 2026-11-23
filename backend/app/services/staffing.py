from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Brand, Organization, Process, StaffingRequirement
from app.services.calendars import InvalidCalendarInput
from app.services.organization_structure import DuplicateResource, ResourceConflict, ResourceNotFound


def _lock_organization(session: Session, organization_id: int) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    organization = session.scalar(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    )
    if organization is None:
        raise ResourceNotFound("Organization not found.")


def _commit(session: Session, row):
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflict("The staffing requirement conflicts with existing data.") from exc
    session.refresh(row)
    return row


def _related_brand_process(
    session: Session, organization_id: int, brand_id: int, process_id: int | None
) -> tuple[Brand, Process | None]:
    brand = session.scalar(
        select(Brand).where(Brand.id == brand_id, Brand.organization_id == organization_id)
    )
    if brand is None:
        raise ResourceNotFound("Brand not found in this organization.")
    process = None
    if process_id is not None:
        process = session.scalar(
            select(Process).where(Process.id == process_id, Process.organization_id == organization_id)
        )
        if process is None:
            raise ResourceNotFound("Process not found in this organization.")
        if process.brand_id != brand.id or process.pod_id != brand.pod_id:
            raise InvalidCalendarInput("The selected Process must belong to the selected Brand.")
    return brand, process


def _overlap_check(session: Session, organization_id: int, values: dict, exclude_id: int | None = None) -> None:
    conditions = [
        StaffingRequirement.organization_id == organization_id,
        StaffingRequirement.brand_id == values["brand_id"],
        StaffingRequirement.process_id == values["process_id"] if values["process_id"] is not None else StaffingRequirement.process_id.is_(None),
        StaffingRequirement.day_of_week == values["day_of_week"] if values["day_of_week"] is not None else StaffingRequirement.day_of_week.is_(None),
        StaffingRequirement.override_date == values["override_date"] if values["override_date"] is not None else StaffingRequirement.override_date.is_(None),
        or_(StaffingRequirement.effective_to.is_(None), StaffingRequirement.effective_to >= values["effective_from"]),
    ]
    if values["effective_to"] is not None:
        conditions.append(StaffingRequirement.effective_from <= values["effective_to"])
    if exclude_id is not None:
        conditions.append(StaffingRequirement.id != exclude_id)
    if session.scalar(select(StaffingRequirement.id).where(*conditions).limit(1)) is not None:
        raise DuplicateResource("The staffing requirement's effective period overlaps an existing rule.")


def list_requirements(
    session: Session,
    organization_id: int,
    brand_id: int | None = None,
    process_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[StaffingRequirement]:
    if session.get(Organization, organization_id) is None:
        raise ResourceNotFound("Organization not found.")
    if brand_id is not None and session.scalar(select(Brand.id).where(Brand.id == brand_id, Brand.organization_id == organization_id)) is None:
        raise ResourceNotFound("Brand not found in this organization.")
    if process_id is not None:
        process = session.scalar(select(Process).where(Process.id == process_id, Process.organization_id == organization_id))
        if process is None:
            raise ResourceNotFound("Process not found in this organization.")
        if brand_id is not None and process.brand_id != brand_id:
            raise InvalidCalendarInput("The selected Process does not belong to the selected Brand.")
    statement = select(StaffingRequirement).where(StaffingRequirement.organization_id == organization_id)
    if brand_id is not None:
        statement = statement.where(StaffingRequirement.brand_id == brand_id)
    if process_id is not None:
        statement = statement.where(StaffingRequirement.process_id == process_id)
    if start_date is not None:
        statement = statement.where(StaffingRequirement.effective_from <= end_date)
        statement = statement.where(or_(StaffingRequirement.effective_to.is_(None), StaffingRequirement.effective_to >= start_date))
    return list(session.scalars(statement.order_by(StaffingRequirement.brand_id, StaffingRequirement.process_id, StaffingRequirement.day_of_week, StaffingRequirement.override_date, StaffingRequirement.effective_from)))


def get_effective_requirement(
    session: Session,
    organization_id: int,
    brand_id: int,
    requested_date: date,
    process_id: int | None,
) -> dict:
    brand, process = _related_brand_process(session, organization_id, brand_id, process_id)
    statement = select(StaffingRequirement).where(
        StaffingRequirement.organization_id == organization_id,
        StaffingRequirement.brand_id == brand.id,
        StaffingRequirement.effective_from <= requested_date,
        or_(StaffingRequirement.effective_to.is_(None), StaffingRequirement.effective_to >= requested_date),
    )
    candidates = list(session.scalars(statement))
    applicable = []
    for row in candidates:
        if row.process_id is not None and (process is None or row.process_id != process.id):
            continue
        if row.override_date is not None and row.override_date != requested_date:
            continue
        if row.day_of_week is not None and row.day_of_week != requested_date.weekday():
            continue
        if row.override_date is not None:
            rank, scope = (0 if row.process_id is not None else 1), "process_date" if row.process_id is not None else "brand_date"
        elif row.day_of_week is not None:
            rank, scope = (2 if row.process_id is not None else 3), "process_weekday" if row.process_id is not None else "brand_weekday"
        else:
            rank, scope = (4 if row.process_id is not None else 5), "process_default" if row.process_id is not None else "brand_default"
        applicable.append((rank, row, scope))
    if not applicable:
        raise ResourceNotFound("No staffing requirement applies to the selected date and scope.")
    _, row, scope = min(applicable, key=lambda item: item[0])
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "pod_id": row.pod_id,
        "brand_id": row.brand_id,
        "process_id": row.process_id,
        "day_of_week": row.day_of_week,
        "override_date": row.override_date,
        "minimum_staff": row.minimum_staff,
        "effective_from": row.effective_from,
        "effective_to": row.effective_to,
        "requested_date": requested_date,
        "scope": scope,
    }


def get_requirement(session: Session, organization_id: int, requirement_id: int) -> StaffingRequirement:
    row = session.scalar(select(StaffingRequirement).where(StaffingRequirement.id == requirement_id, StaffingRequirement.organization_id == organization_id))
    if row is None:
        raise ResourceNotFound("Staffing requirement not found in this organization.")
    return row


def create_requirement(session: Session, organization_id: int, values: dict) -> StaffingRequirement:
    _lock_organization(session, organization_id)
    brand, _ = _related_brand_process(session, organization_id, values["brand_id"], values["process_id"])
    _overlap_check(session, organization_id, values)
    return _commit(
        session,
        StaffingRequirement(
            organization_id=organization_id,
            pod_id=brand.pod_id,
            **values,
        ),
    )


def update_requirement(
    session: Session, organization_id: int, requirement_id: int, values: dict
) -> StaffingRequirement:
    _lock_organization(session, organization_id)
    row = get_requirement(session, organization_id, requirement_id)
    brand, _ = _related_brand_process(session, organization_id, values["brand_id"], values["process_id"])
    _overlap_check(session, organization_id, values, exclude_id=requirement_id)
    for key, value in values.items():
        setattr(row, key, value)
    row.pod_id = brand.pod_id
    return _commit(session, row)


def delete_requirement(session: Session, organization_id: int, requirement_id: int) -> None:
    _lock_organization(session, organization_id)
    row = get_requirement(session, organization_id, requirement_id)
    session.delete(row)
    session.commit()
