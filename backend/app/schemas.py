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


class OperatingRuleInput(RequestModel):
    day_of_week: Annotated[int, Field(ge=0, le=6)]
    sequence: Annotated[int, Field(ge=1)] = 1
    is_open: bool = True
    opens_at: time | None = None
    closes_at: time | None = None
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_rule(self):
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        if self.is_open:
            if self.opens_at is None or self.closes_at is None or self.opens_at == self.closes_at:
                raise ValueError("open rules require distinct opens_at and closes_at values")
        elif self.opens_at is not None or self.closes_at is not None:
            raise ValueError("closed rules cannot include operating hours")
        return self


class ProcessCalendarUpdate(RequestModel):
    timezone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    rules: list[OperatingRuleInput]

    @model_validator(mode="after")
    def validate_calendar_rule_windows(self):
        for index, rule in enumerate(self.rules):
            for other in self.rules[index + 1 :]:
                if (rule.day_of_week, rule.sequence) != (other.day_of_week, other.sequence):
                    continue
                if rule.effective_from == other.effective_from:
                    raise ValueError("calendar rules must have unique weekday, sequence and start date")
                if rule.effective_to is None or other.effective_from <= rule.effective_to:
                    if other.effective_to is None or rule.effective_from <= other.effective_to:
                        raise ValueError("calendar rule effective date windows must not overlap")
        return self

    @model_validator(mode="after")
    def validate_rule_windows(self):
        for index, rule in enumerate(self.rules):
            for other in self.rules[index + 1 :]:
                if (rule.day_of_week, rule.sequence) != (other.day_of_week, other.sequence):
                    continue
                if rule.effective_from == other.effective_from:
                    raise ValueError("calendar rules must have unique weekday, sequence and start date")
                if rule.effective_to is None or other.effective_from <= rule.effective_to:
                    if other.effective_to is None or rule.effective_from <= other.effective_to:
                        raise ValueError("calendar rule effective date windows must not overlap")
        return self


class DayOverrideInput(OperatingRuleInput):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)] | None = None


class DateOverrideInput(RequestModel):
    override_date: date
    sequence: Annotated[int, Field(ge=1)] = 1
    is_open: bool
    opens_at: time | None = None
    closes_at: time | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)] | None = None

    @model_validator(mode="after")
    def validate_override(self):
        if self.is_open:
            if self.opens_at is None or self.closes_at is None or self.opens_at == self.closes_at:
                raise ValueError("open overrides require distinct opens_at and closes_at values")
        elif self.opens_at is not None or self.closes_at is not None:
            raise ValueError("closed overrides cannot include operating hours")
        return self


class EffectiveOperatingDay(OrmRead):
    operating_date: date
    is_holiday: bool
    source: str
    is_open: bool
    sequence: int
    opens_at: time | None = None
    closes_at: time | None = None
    reason: str | None = None


class HolidayCreate(RequestModel):
    holiday_date: date
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    region: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None


class HolidayUpdate(HolidayCreate):
    pass


class HolidayOverrideInput(RequestModel):
    holiday_id: Annotated[int, Field(gt=0)]
    sequence: Annotated[int, Field(ge=1)] = 1
    is_open: bool
    opens_at: time | None = None
    closes_at: time | None = None

    @model_validator(mode="after")
    def validate_override(self):
        if self.is_open:
            if self.opens_at is None or self.closes_at is None or self.opens_at == self.closes_at:
                raise ValueError("open overrides require distinct opens_at and closes_at values")
        elif self.opens_at is not None or self.closes_at is not None:
            raise ValueError("closed overrides cannot include operating hours")
        return self


class StaffingRequirementInput(RequestModel):
    brand_id: Annotated[int, Field(gt=0)]
    process_id: Annotated[int, Field(gt=0)] | None = None
    day_of_week: Annotated[int, Field(ge=0, le=6)] | None = None
    override_date: date | None = None
    minimum_staff: Annotated[int, Field(ge=0)]
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_staffing_scope(self):
        if self.override_date is not None:
            if self.day_of_week is not None:
                raise ValueError("date-specific overrides cannot set day_of_week")
            if self.effective_from not in (None, self.override_date):
                raise ValueError("effective_from must match override_date")
            if self.effective_to not in (None, self.override_date):
                raise ValueError("effective_to must match override_date")
            self.effective_from = self.override_date
            self.effective_to = self.override_date
        elif self.effective_from is None:
            raise ValueError("effective_from is required for non-date-specific requirements")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        return self


class GenerateMonthlyRosterRequest(RequestModel):
    pod_id: Annotated[int, Field(gt=0)]
    year: Annotated[int, Field(ge=2000, le=2200)]
    month: Annotated[int, Field(ge=1, le=12)]
    process_id: Annotated[int, Field(gt=0)] | None = None
    solver_time_limit_seconds: Annotated[int, Field(ge=1, le=60)] = 30
    preserve_locks_from_roster_id: Annotated[int, Field(gt=0)] | None = None


class GeneratedAssignmentRead(OrmRead):
    assignment_id: int
    employee_id: int
    employee_name: str
    process_id: int
    process_name: str
    brand_id: int
    brand_name: str
    process_assignment_id: int
    work_date: date
    starts_at: datetime
    ends_at: datetime
    is_locked: bool = False


class GeneratedEmployeeStatusRead(OrmRead):
    employee_id: int
    employee_name: str
    work_date: date
    status: str


class CoverageRead(OrmRead):
    scope: str
    work_date: date
    brand_id: int
    process_id: int | None = None
    sequence: int | None = None
    required: int
    scheduled: int | None = None
    uncovered: int | None = None
    diagnosed: bool = True


class MonthlyRosterGenerationRead(OrmRead):
    success: bool
    generation_run_id: int
    roster_id: int | None = None
    roster_version: int | None = None
    status: str
    solver_status: str
    year: int
    month: int
    pod_id: int
    assignments: list[GeneratedAssignmentRead] = Field(default_factory=list)
    employee_statuses: list[GeneratedEmployeeStatusRead] = Field(default_factory=list)
    coverage: list[CoverageRead] = Field(default_factory=list)
    error: str | None = None


class StaffingRequirementRead(OrmRead):
    id: int
    organization_id: int
    pod_id: int
    brand_id: int
    process_id: int | None = None
    day_of_week: int | None = None
    override_date: date | None = None
    minimum_staff: int
    effective_from: date
    effective_to: date | None = None


class EffectiveStaffingRequirementRead(StaffingRequirementRead):
    requested_date: date
    scope: str


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
    organization_id: int
    pod_id: int
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


class ProcessCalendarRead(OrmRead):
    process_id: int
    organization_id: int
    timezone: str
    rules: list[OperatingRuleRead]


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
    is_locked: bool = False


class RosterAssignmentEdit(RequestModel):
    employee_id: Annotated[int, Field(gt=0)]
    process_assignment_id: Annotated[int, Field(gt=0)]


class RosterAssignmentLock(RequestModel):
    is_locked: bool


class RosterConflictRead(OrmRead):
    conflict_type: str
    work_date: date
    employee_ids: list[int] = Field(default_factory=list)
    brand_id: int | None = None
    process_id: int | None = None
    severity: str
    message: str
    assignment_ids: list[int] = Field(default_factory=list)
    replacement_employee_ids: list[int] = Field(default_factory=list)


class RosterValidationRead(OrmRead):
    roster_id: int
    valid: bool
    conflict_count: int
    conflicts: list[RosterConflictRead] = Field(default_factory=list)


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
