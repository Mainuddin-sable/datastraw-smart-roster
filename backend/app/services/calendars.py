from datetime import date, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    DateOverride,
    DayOverride,
    Holiday,
    HolidayOverride,
    OperatingRule,
    Organization,
    Process,
)
from app.services.organization_structure import DuplicateResource, ResourceConflict, ResourceNotFound


class InvalidCalendarInput(Exception):
    pass


def _commit(session: Session, resource):
    session.add(resource)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflict("The change conflicts with an existing calendar record.") from exc
    session.refresh(resource)
    return resource


def _process(session: Session, organization_id: int, process_id: int, lock: bool = False) -> Process:
    if lock and session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    statement = select(Process).where(
        Process.id == process_id, Process.organization_id == organization_id
    )
    if lock:
        statement = statement.with_for_update()
    process = session.scalar(statement)
    if process is None:
        raise ResourceNotFound("Process not found in this organization.")
    return process


def _validate_timezone(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidCalendarInput("timezone must be a valid IANA timezone name.") from exc


def _check_period_overlap(
    session: Session,
    model,
    *,
    process_id: int,
    day_of_week: int,
    sequence: int,
    effective_from: date,
    effective_to: date | None,
    exclude_id: int | None = None,
) -> None:
    conditions = [
        model.process_id == process_id,
        model.day_of_week == day_of_week,
        model.sequence == sequence,
        or_(model.effective_to.is_(None), model.effective_to >= effective_from),
    ]
    if effective_to is not None:
        conditions.append(model.effective_from <= effective_to)
    if exclude_id is not None:
        conditions.append(model.id != exclude_id)
    if session.scalar(select(model.id).where(*conditions).limit(1)) is not None:
        raise DuplicateResource("The rule's effective date range overlaps an existing rule.")


def get_calendar(session: Session, organization_id: int, process_id: int) -> dict:
    process = _process(session, organization_id, process_id)
    rules = session.scalars(
        select(OperatingRule).where(OperatingRule.process_id == process_id).order_by(
            OperatingRule.day_of_week, OperatingRule.sequence, OperatingRule.effective_from
        )
    )
    return {
        "process_id": process.id,
        "organization_id": process.organization_id,
        "timezone": process.timezone,
        "rules": list(rules),
    }


def upsert_calendar(session: Session, organization_id: int, process_id: int, timezone: str, rules: list[dict]) -> dict:
    _validate_timezone(timezone)
    process = _process(session, organization_id, process_id, lock=True)
    process.timezone = timezone
    for values in rules:
        existing = session.scalar(
            select(OperatingRule).where(
                OperatingRule.process_id == process_id,
                OperatingRule.day_of_week == values["day_of_week"],
                OperatingRule.sequence == values["sequence"],
                OperatingRule.effective_from == values["effective_from"],
            )
        )
        _check_period_overlap(
            session,
            OperatingRule,
            process_id=process_id,
            day_of_week=values["day_of_week"],
            sequence=values["sequence"],
            effective_from=values["effective_from"],
            effective_to=values["effective_to"],
            exclude_id=existing.id if existing is not None else None,
        )
        if existing is None:
            session.add(
                OperatingRule(
                    organization_id=process.organization_id,
                    pod_id=process.pod_id,
                    process_id=process.id,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflict("The calendar update conflicts with an existing rule.") from exc
    session.refresh(process)
    return get_calendar(session, organization_id, process_id)


def _calendar_override(model, process: Process, values: dict, *, override_date: date | None = None):
    payload = dict(values)
    payload.pop("reason", None) if model is HolidayOverride else None
    return model(
        organization_id=process.organization_id,
        pod_id=process.pod_id,
        process_id=process.id,
        **payload,
    )


def create_day_override(session: Session, organization_id: int, process_id: int, values: dict) -> DayOverride:
    process = _process(session, organization_id, process_id, lock=True)
    _check_period_overlap(
        session, DayOverride, process_id=process_id,
        day_of_week=values["day_of_week"], sequence=values["sequence"],
        effective_from=values["effective_from"], effective_to=values["effective_to"],
    )
    return _commit(session, _calendar_override(DayOverride, process, values))


def list_day_overrides(session: Session, organization_id: int, process_id: int) -> list[DayOverride]:
    _process(session, organization_id, process_id)
    return list(session.scalars(select(DayOverride).where(DayOverride.process_id == process_id).order_by(DayOverride.day_of_week, DayOverride.sequence, DayOverride.effective_from)))


def update_day_override(session: Session, organization_id: int, process_id: int, override_id: int, values: dict) -> DayOverride:
    process = _process(session, organization_id, process_id, lock=True)
    row = session.scalar(select(DayOverride).where(DayOverride.id == override_id, DayOverride.process_id == process_id))
    if row is None:
        raise ResourceNotFound("Day override not found for this process.")
    _check_period_overlap(
        session, DayOverride, process_id=process_id,
        day_of_week=values["day_of_week"], sequence=values["sequence"],
        effective_from=values["effective_from"], effective_to=values["effective_to"], exclude_id=override_id,
    )
    for key, value in values.items():
        setattr(row, key, value)
    row.organization_id, row.pod_id = process.organization_id, process.pod_id
    return _commit(session, row)


def delete_day_override(session: Session, organization_id: int, process_id: int, override_id: int) -> None:
    _process(session, organization_id, process_id)
    row = session.scalar(select(DayOverride).where(DayOverride.id == override_id, DayOverride.process_id == process_id))
    if row is None:
        raise ResourceNotFound("Day override not found for this process.")
    session.delete(row)
    session.commit()


def create_date_override(session: Session, organization_id: int, process_id: int, values: dict) -> DateOverride:
    process = _process(session, organization_id, process_id, lock=True)
    if session.scalar(select(DateOverride.id).where(DateOverride.process_id == process_id, DateOverride.override_date == values["override_date"], DateOverride.sequence == values["sequence"])) is not None:
        raise DuplicateResource("A date override already exists for this date and sequence.")
    return _commit(session, _calendar_override(DateOverride, process, values))


def list_date_overrides(session: Session, organization_id: int, process_id: int, start_date: date, end_date: date) -> list[DateOverride]:
    _process(session, organization_id, process_id)
    return list(session.scalars(select(DateOverride).where(DateOverride.process_id == process_id, DateOverride.override_date >= start_date, DateOverride.override_date <= end_date).order_by(DateOverride.override_date, DateOverride.sequence)))


def update_date_override(session: Session, organization_id: int, process_id: int, override_id: int, values: dict) -> DateOverride:
    process = _process(session, organization_id, process_id, lock=True)
    row = session.scalar(select(DateOverride).where(DateOverride.id == override_id, DateOverride.process_id == process_id))
    if row is None:
        raise ResourceNotFound("Date override not found for this process.")
    duplicate = session.scalar(select(DateOverride.id).where(DateOverride.process_id == process_id, DateOverride.override_date == values["override_date"], DateOverride.sequence == values["sequence"], DateOverride.id != override_id))
    if duplicate is not None:
        raise DuplicateResource("A date override already exists for this date and sequence.")
    for key, value in values.items():
        setattr(row, key, value)
    row.organization_id, row.pod_id = process.organization_id, process.pod_id
    return _commit(session, row)


def delete_date_override(session: Session, organization_id: int, process_id: int, override_id: int) -> None:
    _process(session, organization_id, process_id)
    row = session.scalar(select(DateOverride).where(DateOverride.id == override_id, DateOverride.process_id == process_id))
    if row is None:
        raise ResourceNotFound("Date override not found for this process.")
    session.delete(row)
    session.commit()


def list_holidays(session: Session, organization_id: int, start_date: date, end_date: date) -> list[Holiday]:
    if session.get(Organization, organization_id) is None:
        raise ResourceNotFound("Organization not found.")
    return list(session.scalars(select(Holiday).where(Holiday.organization_id == organization_id, Holiday.holiday_date >= start_date, Holiday.holiday_date <= end_date).order_by(Holiday.holiday_date)))


def create_holiday(session: Session, organization_id: int, holiday_date: date, name: str, region: str | None) -> Holiday:
    if session.get(Organization, organization_id) is None:
        raise ResourceNotFound("Organization not found.")
    if session.scalar(select(Holiday.id).where(Holiday.organization_id == organization_id, Holiday.holiday_date == holiday_date)) is not None:
        raise DuplicateResource("A holiday already exists for this organization and date.")
    return _commit(session, Holiday(organization_id=organization_id, holiday_date=holiday_date, name=name, region=region))


def get_holiday(session: Session, organization_id: int, holiday_id: int) -> Holiday:
    row = session.scalar(select(Holiday).where(Holiday.id == holiday_id, Holiday.organization_id == organization_id))
    if row is None:
        raise ResourceNotFound("Holiday not found in this organization.")
    return row


def update_holiday(session: Session, organization_id: int, holiday_id: int, holiday_date: date, name: str, region: str | None) -> Holiday:
    row = get_holiday(session, organization_id, holiday_id)
    duplicate = session.scalar(select(Holiday.id).where(Holiday.organization_id == organization_id, Holiday.holiday_date == holiday_date, Holiday.id != holiday_id))
    if duplicate is not None:
        raise DuplicateResource("A holiday already exists for this organization and date.")
    row.holiday_date, row.name, row.region = holiday_date, name, region
    return _commit(session, row)


def delete_holiday(session: Session, organization_id: int, holiday_id: int) -> None:
    row = get_holiday(session, organization_id, holiday_id)
    session.delete(row)
    session.commit()


def create_holiday_override(session: Session, organization_id: int, process_id: int, values: dict) -> HolidayOverride:
    process = _process(session, organization_id, process_id, lock=True)
    holiday = session.scalar(select(Holiday).where(Holiday.id == values["holiday_id"], Holiday.organization_id == organization_id))
    if holiday is None:
        raise ResourceNotFound("Holiday not found in this organization.")
    if session.scalar(select(HolidayOverride.id).where(HolidayOverride.process_id == process_id, HolidayOverride.holiday_id == holiday.id, HolidayOverride.sequence == values["sequence"])) is not None:
        raise DuplicateResource("A holiday override already exists for this sequence.")
    return _commit(session, HolidayOverride(organization_id=organization_id, pod_id=process.pod_id, process_id=process_id, **values))


def list_holiday_overrides(session: Session, organization_id: int, process_id: int) -> list[HolidayOverride]:
    _process(session, organization_id, process_id)
    return list(session.scalars(select(HolidayOverride).where(HolidayOverride.process_id == process_id).order_by(HolidayOverride.holiday_id, HolidayOverride.sequence)))


def update_holiday_override(session: Session, organization_id: int, process_id: int, override_id: int, values: dict) -> HolidayOverride:
    process = _process(session, organization_id, process_id, lock=True)
    row = session.scalar(select(HolidayOverride).where(HolidayOverride.id == override_id, HolidayOverride.process_id == process_id))
    if row is None:
        raise ResourceNotFound("Holiday override not found for this process.")
    holiday = session.scalar(select(Holiday).where(Holiday.id == values["holiday_id"], Holiday.organization_id == organization_id))
    if holiday is None:
        raise ResourceNotFound("Holiday not found in this organization.")
    duplicate = session.scalar(select(HolidayOverride.id).where(HolidayOverride.process_id == process_id, HolidayOverride.holiday_id == holiday.id, HolidayOverride.sequence == values["sequence"], HolidayOverride.id != override_id))
    if duplicate is not None:
        raise DuplicateResource("A holiday override already exists for this sequence.")
    for key, value in values.items():
        setattr(row, key, value)
    row.organization_id, row.pod_id = process.organization_id, process.pod_id
    return _commit(session, row)


def delete_holiday_override(session: Session, organization_id: int, process_id: int, override_id: int) -> None:
    _process(session, organization_id, process_id)
    row = session.scalar(select(HolidayOverride).where(HolidayOverride.id == override_id, HolidayOverride.process_id == process_id))
    if row is None:
        raise ResourceNotFound("Holiday override not found for this process.")
    session.delete(row)
    session.commit()


def get_effective_calendar(session: Session, organization_id: int, process_id: int, start_date: date, end_date: date) -> list[dict]:
    """Resolve effective operating intervals; absent rules intentionally default closed."""
    process = _process(session, organization_id, process_id)
    holidays = list(session.scalars(select(Holiday).where(Holiday.organization_id == organization_id, Holiday.holiday_date >= start_date, Holiday.holiday_date <= end_date)))
    holiday_by_date = {row.holiday_date: row for row in holidays}
    day_range = (end_date - start_date).days
    weekdays = {(start_date + timedelta(days=offset)).weekday() for offset in range(day_range + 1)}
    rules = list(session.scalars(select(OperatingRule).where(OperatingRule.process_id == process_id, OperatingRule.day_of_week.in_(weekdays), OperatingRule.effective_from <= end_date, or_(OperatingRule.effective_to.is_(None), OperatingRule.effective_to >= start_date))))
    day_overrides = list(session.scalars(select(DayOverride).where(DayOverride.process_id == process_id, DayOverride.day_of_week.in_(weekdays), DayOverride.effective_from <= end_date, or_(DayOverride.effective_to.is_(None), DayOverride.effective_to >= start_date))))
    date_overrides = list(session.scalars(select(DateOverride).where(DateOverride.process_id == process_id, DateOverride.override_date >= start_date, DateOverride.override_date <= end_date)))
    holiday_ids = [row.id for row in holidays]
    holiday_overrides = list(session.scalars(select(HolidayOverride).where(HolidayOverride.process_id == process_id, HolidayOverride.holiday_id.in_(holiday_ids)))) if holiday_ids else []
    by_date = {}
    for row in date_overrides:
        by_date.setdefault(row.override_date, []).append(row)
    by_holiday = {}
    for row in holiday_overrides:
        by_holiday.setdefault(row.holiday_id, []).append(row)
    by_day_override, by_rule = {}, {}
    for row in day_overrides:
        by_day_override.setdefault(row.day_of_week, []).append(row)
    for row in rules:
        by_rule.setdefault(row.day_of_week, []).append(row)

    result = []
    for offset in range(day_range + 1):
        current = start_date + timedelta(days=offset)
        holiday = holiday_by_date.get(current)
        if by_date.get(current):
            source, rows = "date_override", by_date[current]
        elif holiday and by_holiday.get(holiday.id):
            source, rows = "holiday_override", by_holiday[holiday.id]
        elif holiday:
            source, rows = "holiday", []
        else:
            applicable_day_overrides = [r for r in by_day_override.get(current.weekday(), []) if r.effective_from <= current and (r.effective_to is None or r.effective_to >= current)]
            applicable_rules = [r for r in by_rule.get(current.weekday(), []) if r.effective_from <= current and (r.effective_to is None or r.effective_to >= current)]
            if applicable_day_overrides:
                source, rows = "day_override", applicable_day_overrides
            else:
                source, rows = "operating_rule", applicable_rules
        if source == "holiday" or not rows:
            result.append({"operating_date": current, "is_holiday": holiday is not None, "source": source if rows or holiday else "closed_default", "is_open": False, "sequence": 1, "opens_at": None, "closes_at": None, "reason": holiday.name if holiday else "No operating rule configured"})
        else:
            for row in sorted(rows, key=lambda item: item.sequence):
                result.append({"operating_date": current, "is_holiday": holiday is not None, "source": source, "is_open": row.is_open, "sequence": row.sequence, "opens_at": row.opens_at, "closes_at": row.closes_at, "reason": getattr(row, "reason", None) or (holiday.name if holiday else None)})
    return result
