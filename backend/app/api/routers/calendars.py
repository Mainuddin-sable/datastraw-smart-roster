from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import (
    DateOverrideInput,
    DateOverrideRead,
    DayOverrideInput,
    DayOverrideRead,
    EffectiveOperatingDay,
    HolidayCreate,
    HolidayOverrideInput,
    HolidayOverrideRead,
    HolidayRead,
    HolidayUpdate,
    OperatingRuleRead,
    ProcessCalendarRead,
    ProcessCalendarUpdate,
)
from app.services import calendars as service

router = APIRouter(tags=["operating calendars", "holidays"])
DbSession = Annotated[Session, Depends(get_db)]
OrgId = Annotated[int, Path(gt=0)]
ProcessId = Annotated[int, Path(gt=0)]
RecordId = Annotated[int, Path(gt=0)]


def _validate_range(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")


@router.get("/organizations/{organization_id}/processes/{process_id}/calendar", response_model=ProcessCalendarRead, responses={404: {"description": "Process not found in this organization"}})
def get_calendar(organization_id: OrgId, process_id: ProcessId, db: DbSession):
    """Get a Process timezone and its recurring operating rules."""
    return service.get_calendar(db, organization_id, process_id)


@router.put("/organizations/{organization_id}/processes/{process_id}/calendar", response_model=ProcessCalendarRead, responses={404: {"description": "Process not found in this organization"}, 409: {"description": "Overlapping operating rules"}, 422: {"description": "Invalid timezone or rule"}})
def update_calendar(organization_id: OrgId, process_id: ProcessId, payload: ProcessCalendarUpdate, db: DbSession):
    """Create or upsert recurring rules and set the Process timezone. Omitted rules are retained."""
    return service.upsert_calendar(
        db, organization_id, process_id, payload.timezone,
        [rule.model_dump() for rule in payload.rules],
    )


@router.get("/organizations/{organization_id}/processes/{process_id}/calendar/effective", response_model=list[EffectiveOperatingDay], responses={404: {"description": "Process not found in this organization"}, 422: {"description": "Invalid date range"}})
def get_effective_calendar(
    organization_id: OrgId,
    process_id: ProcessId,
    db: DbSession,
    start_date: Annotated[date, Query()],
    end_date: Annotated[date, Query()],
):
    """Resolve daily operating hours with date, holiday, weekly, then base-rule precedence."""
    _validate_range(start_date, end_date)
    return service.get_effective_calendar(db, organization_id, process_id, start_date, end_date)


@router.post("/organizations/{organization_id}/processes/{process_id}/day-overrides", response_model=DayOverrideRead, status_code=status.HTTP_201_CREATED, responses={404: {"description": "Process not found in this organization"}, 409: {"description": "Overlapping weekly override"}})
def create_day_override(organization_id: OrgId, process_id: ProcessId, payload: DayOverrideInput, db: DbSession):
    """Create a recurring weekday exception to normal operating rules."""
    return service.create_day_override(db, organization_id, process_id, payload.model_dump())


@router.get("/organizations/{organization_id}/processes/{process_id}/day-overrides", response_model=list[DayOverrideRead])
def list_day_overrides(organization_id: OrgId, process_id: ProcessId, db: DbSession):
    return service.list_day_overrides(db, organization_id, process_id)


@router.put("/organizations/{organization_id}/processes/{process_id}/day-overrides/{override_id}", response_model=DayOverrideRead, responses={404: {"description": "Process or override not found"}, 409: {"description": "Overlapping weekly override"}})
def update_day_override(organization_id: OrgId, process_id: ProcessId, override_id: RecordId, payload: DayOverrideInput, db: DbSession):
    return service.update_day_override(db, organization_id, process_id, override_id, payload.model_dump())


@router.delete("/organizations/{organization_id}/processes/{process_id}/day-overrides/{override_id}", status_code=204, responses={404: {"description": "Process or override not found"}})
def delete_day_override(organization_id: OrgId, process_id: ProcessId, override_id: RecordId, db: DbSession):
    service.delete_day_override(db, organization_id, process_id, override_id)
    return Response(status_code=204)


@router.post("/organizations/{organization_id}/processes/{process_id}/date-overrides", response_model=DateOverrideRead, status_code=201, responses={404: {"description": "Process not found in this organization"}, 409: {"description": "Duplicate date override"}})
def create_date_override(organization_id: OrgId, process_id: ProcessId, payload: DateOverrideInput, db: DbSession):
    return service.create_date_override(db, organization_id, process_id, payload.model_dump())


@router.get("/organizations/{organization_id}/processes/{process_id}/date-overrides", response_model=list[DateOverrideRead])
def list_date_overrides(
    organization_id: OrgId,
    process_id: ProcessId,
    db: DbSession,
    start_date: Annotated[date, Query()],
    end_date: Annotated[date, Query()],
):
    _validate_range(start_date, end_date)
    return service.list_date_overrides(db, organization_id, process_id, start_date, end_date)


@router.put("/organizations/{organization_id}/processes/{process_id}/date-overrides/{override_id}", response_model=DateOverrideRead, responses={404: {"description": "Process or override not found"}, 409: {"description": "Duplicate date override"}})
def update_date_override(organization_id: OrgId, process_id: ProcessId, override_id: RecordId, payload: DateOverrideInput, db: DbSession):
    return service.update_date_override(db, organization_id, process_id, override_id, payload.model_dump())


@router.delete("/organizations/{organization_id}/processes/{process_id}/date-overrides/{override_id}", status_code=204)
def delete_date_override(organization_id: OrgId, process_id: ProcessId, override_id: RecordId, db: DbSession):
    service.delete_date_override(db, organization_id, process_id, override_id)
    return Response(status_code=204)


@router.post("/organizations/{organization_id}/processes/{process_id}/holiday-overrides", response_model=HolidayOverrideRead, status_code=201, responses={404: {"description": "Process or organization holiday not found"}, 409: {"description": "Duplicate holiday override"}})
def create_holiday_override(organization_id: OrgId, process_id: ProcessId, payload: HolidayOverrideInput, db: DbSession):
    return service.create_holiday_override(db, organization_id, process_id, payload.model_dump())


@router.get("/organizations/{organization_id}/processes/{process_id}/holiday-overrides", response_model=list[HolidayOverrideRead])
def list_holiday_overrides(organization_id: OrgId, process_id: ProcessId, db: DbSession):
    return service.list_holiday_overrides(db, organization_id, process_id)


@router.put("/organizations/{organization_id}/processes/{process_id}/holiday-overrides/{override_id}", response_model=HolidayOverrideRead, responses={404: {"description": "Process, override, or holiday not found"}, 409: {"description": "Duplicate holiday override"}})
def update_holiday_override(organization_id: OrgId, process_id: ProcessId, override_id: RecordId, payload: HolidayOverrideInput, db: DbSession):
    return service.update_holiday_override(db, organization_id, process_id, override_id, payload.model_dump())


@router.delete("/organizations/{organization_id}/processes/{process_id}/holiday-overrides/{override_id}", status_code=204)
def delete_holiday_override(organization_id: OrgId, process_id: ProcessId, override_id: RecordId, db: DbSession):
    service.delete_holiday_override(db, organization_id, process_id, override_id)
    return Response(status_code=204)


@router.post("/organizations/{organization_id}/holidays", response_model=HolidayRead, status_code=201, responses={404: {"description": "Organization not found"}, 409: {"description": "Holiday date already exists"}})
def create_holiday(organization_id: OrgId, payload: HolidayCreate, db: DbSession):
    return service.create_holiday(db, organization_id, payload.holiday_date, payload.name, payload.region)


@router.get("/organizations/{organization_id}/holidays", response_model=list[HolidayRead], responses={404: {"description": "Organization not found"}, 422: {"description": "Invalid date range"}})
def list_holidays(
    organization_id: OrgId,
    db: DbSession,
    start_date: Annotated[date, Query()],
    end_date: Annotated[date, Query()],
):
    _validate_range(start_date, end_date)
    return service.list_holidays(db, organization_id, start_date, end_date)


@router.get("/organizations/{organization_id}/holidays/{holiday_id}", response_model=HolidayRead, responses={404: {"description": "Holiday not found in this organization"}})
def get_holiday(organization_id: OrgId, holiday_id: RecordId, db: DbSession):
    return service.get_holiday(db, organization_id, holiday_id)


@router.put("/organizations/{organization_id}/holidays/{holiday_id}", response_model=HolidayRead, responses={404: {"description": "Holiday not found in this organization"}, 409: {"description": "Holiday date already exists"}})
def update_holiday(organization_id: OrgId, holiday_id: RecordId, payload: HolidayUpdate, db: DbSession):
    return service.update_holiday(db, organization_id, holiday_id, payload.holiday_date, payload.name, payload.region)


@router.delete("/organizations/{organization_id}/holidays/{holiday_id}", status_code=204, responses={404: {"description": "Holiday not found in this organization"}})
def delete_holiday(organization_id: OrgId, holiday_id: RecordId, db: DbSession):
    service.delete_holiday(db, organization_id, holiday_id)
    return Response(status_code=204)
