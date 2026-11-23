"""Pydantic request and response contracts for roster and organization APIs."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class OrmRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationCreate(RequestModel):
    name: Name


class OrganizationUpdate(RequestModel):
    name: Name


class PodCreate(RequestModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class PodUpdate(PodCreate):
    pass


class BrandCreate(RequestModel):
    pod_id: Annotated[int, Field(gt=0)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class BrandUpdate(BrandCreate):
    pass


class ProcessCreate(RequestModel):
    brand_id: Annotated[int, Field(gt=0)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    timezone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] = "UTC"


class ProcessUpdate(ProcessCreate):
    pass


EmailAddress = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]


class EmployeeCreate(RequestModel):
    pod_id: Annotated[int, Field(gt=0)]
    full_name: Name
    email: EmailAddress
    role: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    is_active: bool = True


class EmployeeUpdate(RequestModel):
    full_name: Name
    email: EmailAddress
    role: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    is_active: bool


class ProcessAssignmentCreate(RequestModel):
    process_id: Annotated[int, Field(gt=0)]
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_effective_dates(self):
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        return self


class ProcessAssignmentUpdate(ProcessAssignmentCreate):
    pass


class ProcessAssignmentDeactivate(RequestModel):
    effective_to: date


class EmployeeAvailabilityCreate(RequestModel):
    day_of_week: Annotated[int, Field(ge=0, le=6)]
    sequence: Annotated[int, Field(ge=1)] = 1
    is_available: bool = True
    available_from: time | None = None
    available_to: time | None = None
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_availability(self):
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        if self.is_available:
            if (self.available_from is None) != (self.available_to is None):
                raise ValueError("available_from and available_to must be provided together")
            if self.available_from == self.available_to and self.available_from is not None:
                raise ValueError("available_from and available_to must differ")
        elif self.available_from is not None or self.available_to is not None:
            raise ValueError("unavailable records cannot include availability times")
        return self


class EmployeeAvailabilityUpdate(EmployeeAvailabilityCreate):
    pass


class EmployeeAvailabilityRange(RequestModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class EffectiveWindowRead(OrmRead):
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_effective_window(self):
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        return self


class OrganizationRead(OrmRead):
    id: int
    name: str
    created_at: datetime


class PodRead(OrmRead):
    id: int
    organization_id: int
    name: str


class BrandRead(OrmRead):
    id: int
    organization_id: int
    pod_id: int
    name: str


class EmployeeRead(OrmRead):
    id: int
    organization_id: int
    pod_id: int
    full_name: str
    email: str
    role: str | None = None
    is_active: bool


class ProcessRead(OrmRead):
    id: int
    organization_id: int
    pod_id: int
    brand_id: int
    name: str
    timezone: str


class ProcessAssignmentRead(EffectiveWindowRead):
    id: int
    organization_id: int
    pod_id: int
    employee_id: int
    process_id: int


class EmployeeEligibleProcessRead(EffectiveWindowRead):
    id: int
    employee_id: int
    process_id: int
    process_name: str
    brand_id: int
    brand_name: str


class OperatingRuleRead(EffectiveWindowRead):
    id: int
    process_id: int
    day_of_week: int
    sequence: int
    is_open: bool
    opens_at: time | None = None
    closes_at: time | None = None


class DayOverrideRead(OperatingRuleRead):
    reason: str | None = None


class DateOverrideRead(OrmRead):
    id: int
    process_id: int
    override_date: date
    sequence: int
    is_open: bool
    opens_at: time | None = None
    closes_at: time | None = None
    reason: str | None = None


class HolidayRead(OrmRead):
    id: int
    organization_id: int
    holiday_date: date
    name: str
    region: str | None = None


class HolidayOverrideRead(OrmRead):
    id: int
    holiday_id: int
    process_id: int
    sequence: int
    is_open: bool
    opens_at: time | None = None
    closes_at: time | None = None


class EmployeeAvailabilityRead(EffectiveWindowRead):
    id: int
    organization_id: int
    pod_id: int
    employee_id: int
    day_of_week: int
    sequence: int
    is_available: bool
    available_from: time | None = None
    available_to: time | None = None


class EmployeeLeaveRead(OrmRead):
    id: int
    employee_id: int
    starts_at: datetime
    ends_at: datetime
    leave_type: str
    status: str
    reason: str | None = None


class EmployeeCompOffRead(OrmRead):
    id: int
    employee_id: int
    earned_on: date
    expires_on: date | None = None
    hours_earned: Decimal
    hours_used: Decimal
    reason: str | None = None


class EmployeePreferenceRead(EffectiveWindowRead):
    id: int
    employee_id: int
    preference_key: str
    value: dict[str, Any]


class RosterRead(OrmRead):
    id: int
    organization_id: int
    pod_id: int
    period_start: date
    period_end: date
    version: int
    status: str
    created_at: datetime


class RosterEntryRead(OrmRead):
    id: int
    roster_id: int
    employee_id: int
    work_date: date
    status: str
    notes: str | None = None


class RosterWorkAssignmentRead(OrmRead):
    id: int
    roster_entry_id: int
    employee_id: int
    process_assignment_id: int
    starts_at: datetime
    ends_at: datetime
    work_label: str | None = None


class GenerationRunRead(OrmRead):
    id: int
    organization_id: int
    pod_id: int
    process_id: int | None = None
    roster_id: int | None = None
    status: str
    parameters: dict[str, Any]
    result_summary: dict[str, Any] | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class AuditLogRead(OrmRead):
    id: int
    organization_id: int
    actor_employee_id: int | None = None
    actor_organization_id: int | None = None
    entity_type: str
    entity_id: int
    action: str
    payload: dict[str, Any]
    created_at: datetime
