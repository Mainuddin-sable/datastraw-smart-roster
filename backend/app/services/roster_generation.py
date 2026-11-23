"""Database preparation and persistence for monthly CP-SAT roster generation."""

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Employee,
    EmployeeAvailability,
    EmployeeLeave,
    EmployeePreference,
    GenerationRun,
    Process,
    ProcessAssignment,
    Pod,
    Roster,
    RosterEntry,
    RosterWorkAssignment,
    StaffingRequirement,
)
from app.scheduling.monthly import (
    EligibleCandidate,
    MonthlyProblem,
    ScheduledBlock,
    StaffingDemand,
    WorkSlot,
    solve_monthly_roster,
)
from app.services.calendars import get_effective_calendar
from app.services.organization_structure import ResourceNotFound


class RosterGenerationError(Exception):
    pass


class LockedAssignmentConflict(ValueError):
    def __init__(self, report: dict):
        self.report = report
        super().__init__("Locked assignments conflict with current hard constraints; unresolved gaps are included.")


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _rule_interval(work_date: date, opens_at: time, closes_at: time, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(work_date, opens_at).replace(tzinfo=tz)
    end_date = work_date + (timedelta(days=1) if closes_at <= opens_at else timedelta())
    end_local = datetime.combine(end_date, closes_at).replace(tzinfo=tz)
    # Reject wall times that fall into a daylight-saving gap instead of silently
    # shifting the configured process operating interval.
    if start_local.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != start_local.replace(tzinfo=None):
        raise RosterGenerationError(f"Operating start time does not exist on {work_date} in {tz.key}.")
    if end_local.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != end_local.replace(tzinfo=None):
        raise RosterGenerationError(f"Operating end time does not exist on {end_date} in {tz.key}.")
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _availability_allows(
    rows: list[EmployeeAvailability],
    employee_id: int,
    work_date: date,
    start_utc: datetime,
    end_utc: datetime,
    tz: ZoneInfo,
) -> bool:
    current = [
        row for row in rows
        if row.employee_id == employee_id
        and row.day_of_week == work_date.weekday()
        and row.effective_from <= work_date
        and (row.effective_to is None or row.effective_to >= work_date)
    ]
    if not current:
        return True
    available = [row for row in current if row.is_available]
    if not available:
        return False
    start_local, end_local = start_utc.astimezone(tz), end_utc.astimezone(tz)
    start_minute = start_local.hour * 60 + start_local.minute
    end_minute = end_local.hour * 60 + end_local.minute
    if end_local.date() > start_local.date():
        end_minute += 24 * 60
    for row in available:
        if row.available_from is None and row.available_to is None:
            return True
        available_start = row.available_from.hour * 60 + row.available_from.minute
        available_end = row.available_to.hour * 60 + row.available_to.minute
        if available_end <= available_start:
            available_end += 24 * 60
        if available_start <= start_minute and end_minute <= available_end:
            return True
    return False


def _leave_overlaps(
    leaves: list[EmployeeLeave], employee_id: int, start: datetime, end: datetime
) -> bool:
    return any(
        row.employee_id == employee_id
        and row.starts_at is not None
        and _as_utc(row.starts_at) < end
        and _as_utc(row.ends_at) > start
        for row in leaves
    )


def _leave_on_date(leaves: list[EmployeeLeave], employee_id: int, work_date: date) -> bool:
    day_start = datetime.combine(work_date, time.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    return any(
        row.employee_id == employee_id
        and _as_utc(row.starts_at) < day_end
        and _as_utc(row.ends_at) > day_start
        for row in leaves
    )


def _preference_cost(
    preferences: list[EmployeePreference], employee_id: int, process_id: int, work_date: date
) -> int:
    cost = 0
    for preference in preferences:
        if preference.employee_id != employee_id or preference.effective_from > work_date:
            continue
        if preference.effective_to is not None and preference.effective_to < work_date:
            continue
        value = preference.value if isinstance(preference.value, dict) else {}
        if preference.preference_key == "preferred_days_off":
            weekdays = value.get("weekdays", [])
            dates = value.get("dates", [])
            if work_date.weekday() in weekdays or work_date.isoformat() in dates:
                cost += 20
        elif preference.preference_key == "preferred_process_ids":
            process_ids = value.get("process_ids", [])
            if process_ids and process_id not in process_ids:
                cost += 10
    return cost


def _active_requirement(
    rows: list[StaffingRequirement],
    work_date: date,
    *,
    process_id: int | None,
) -> StaffingRequirement | None:
    applicable = []
    for row in rows:
        if row.process_id != process_id:
            continue
        if row.effective_from > work_date or (row.effective_to is not None and row.effective_to < work_date):
            continue
        if row.override_date is not None and row.override_date != work_date:
            continue
        if row.day_of_week is not None and row.day_of_week != work_date.weekday():
            continue
        rank = 0 if row.override_date is not None else (1 if row.day_of_week is not None else 2)
        applicable.append((rank, row.effective_from, row))
    return min(applicable, key=lambda value: (value[0], -value[1].toordinal()))[2] if applicable else None


def _make_problem(
    session: Session,
    organization_id: int,
    pod_id: int,
    start_date: date,
    end_date: date,
    requested_process_id: int | None,
    locked_assignments: list[RosterWorkAssignment] | None = None,
) -> tuple[MonthlyProblem, list[Employee], dict[int, Process], list[EmployeeLeave]]:
    employees = list(session.scalars(select(Employee).where(
        Employee.organization_id == organization_id,
        Employee.pod_id == pod_id,
        Employee.is_active.is_(True),
    ).order_by(Employee.id)))
    processes_statement = select(Process).where(
        Process.organization_id == organization_id,
        Process.pod_id == pod_id,
    )
    if requested_process_id is not None:
        processes_statement = processes_statement.where(Process.id == requested_process_id)
    processes = list(session.scalars(processes_statement.order_by(Process.id)))
    process_by_id = {process.id: process for process in processes}
    employee_ids = tuple(employee.id for employee in employees)
    process_ids = tuple(process_by_id)

    assignments = []
    if employee_ids and process_ids:
        assignments = list(session.scalars(select(ProcessAssignment).where(
            ProcessAssignment.organization_id == organization_id,
            ProcessAssignment.pod_id == pod_id,
            ProcessAssignment.employee_id.in_(employee_ids),
            ProcessAssignment.process_id.in_(process_ids),
            ProcessAssignment.effective_from <= end_date,
            or_(ProcessAssignment.effective_to.is_(None), ProcessAssignment.effective_to >= start_date),
        )))
    assignment_by_employee_process: dict[tuple[int, int], list[ProcessAssignment]] = {}
    for row in assignments:
        assignment_by_employee_process.setdefault((row.employee_id, row.process_id), []).append(row)

    availability = list(session.scalars(select(EmployeeAvailability).where(
        EmployeeAvailability.employee_id.in_(employee_ids),
        EmployeeAvailability.effective_from <= end_date,
        or_(EmployeeAvailability.effective_to.is_(None), EmployeeAvailability.effective_to >= start_date),
    ))) if employee_ids else []
    leave_scan_start = datetime.combine(start_date, time.min, tzinfo=timezone.utc) - timedelta(days=2)
    leave_scan_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc) + timedelta(days=2)
    leaves = list(session.scalars(select(EmployeeLeave).where(
        EmployeeLeave.employee_id.in_(employee_ids),
        EmployeeLeave.status == "approved",
        EmployeeLeave.starts_at < leave_scan_end,
        EmployeeLeave.ends_at > leave_scan_start,
    ))) if employee_ids else []
    preferences = list(session.scalars(select(EmployeePreference).where(
        EmployeePreference.employee_id.in_(employee_ids),
        EmployeePreference.effective_from <= end_date,
        or_(EmployeePreference.effective_to.is_(None), EmployeePreference.effective_to >= start_date),
    ))) if employee_ids else []
    staffing = list(session.scalars(select(StaffingRequirement).where(
        StaffingRequirement.organization_id == organization_id,
        StaffingRequirement.pod_id == pod_id,
    )))

    slots: list[WorkSlot] = []
    process_demands: list[StaffingDemand] = []
    brand_demands: list[StaffingDemand] = []
    open_brand_dates: set[tuple[int, date]] = set()
    open_process_dates: set[tuple[int, date]] = set()

    for process in processes:
        try:
            tz = ZoneInfo(process.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise RosterGenerationError(f"Process {process.id} has invalid timezone {process.timezone!r}.") from exc
        effective_days = get_effective_calendar(session, organization_id, process.id, start_date, end_date)
        for effective in effective_days:
            if not effective["is_open"]:
                continue
            work_date = effective["operating_date"]
            open_process_dates.add((process.id, work_date))
            open_brand_dates.add((process.brand_id, work_date))
            starts_at, ends_at = _rule_interval(work_date, effective["opens_at"], effective["closes_at"], tz)
            candidates: dict[int, EligibleCandidate] = {}
            for employee in employees:
                eligible_rows = [
                    assignment for assignment in assignment_by_employee_process.get((employee.id, process.id), [])
                    if assignment.effective_from <= work_date
                    and (assignment.effective_to is None or assignment.effective_to >= work_date)
                ]
                if not eligible_rows:
                    continue
                if _leave_overlaps(leaves, employee.id, starts_at, ends_at):
                    continue
                if not _availability_allows(availability, employee.id, work_date, starts_at, ends_at, tz):
                    continue
                process_assignment = min(eligible_rows, key=lambda assignment: (assignment.effective_from, assignment.id))
                candidates[employee.id] = EligibleCandidate(
                    employee_id=employee.id,
                    process_assignment_id=process_assignment.id,
                    preference_cost=_preference_cost(preferences, employee.id, process.id, work_date),
                )
            for locked in locked_assignments or []:
                locked_entry = locked.roster_entry
                if locked.employee_id not in employee_ids or locked_entry.work_date != work_date:
                    continue
                locked_pa = locked.process_assignment
                if locked_pa.process_id != process.id:
                    continue
                if _as_utc(locked.starts_at) != starts_at or _as_utc(locked.ends_at) != ends_at:
                    raise RosterGenerationError(
                        f"Locked assignment {locked.id} no longer matches its Process operating interval."
                    )
                if not any(row.id == locked.process_assignment_id for row in eligible_rows_for(
                    assignment_by_employee_process, locked.employee_id, process.id, work_date
                )):
                    raise RosterGenerationError(f"Locked assignment {locked.id} is no longer process eligible.")
                candidates[locked.employee_id] = EligibleCandidate(
                    employee_id=locked.employee_id,
                    process_assignment_id=locked.process_assignment_id,
                    locked=True,
                )
            slots.append(WorkSlot(
                slot_id=f"p{process.id}-{work_date.isoformat()}-s{effective['sequence']}",
                work_date=work_date,
                process_id=process.id,
                brand_id=process.brand_id,
                sequence=effective["sequence"],
                starts_at=starts_at,
                ends_at=ends_at,
                candidates=tuple(candidates.values()),
            ))

    for process in processes:
        rows = [row for row in staffing if row.process_id == process.id]
        for work_date in sorted(day for process_id, day in open_process_dates if process_id == process.id):
            requirement = _active_requirement(rows, work_date, process_id=process.id)
            if requirement is not None and requirement.minimum_staff > 0:
                for slot in slots:
                    if slot.process_id == process.id and slot.work_date == work_date:
                        process_demands.append(StaffingDemand(
                            demand_id=f"process-{process.id}-{work_date.isoformat()}-s{slot.sequence}",
                            scope="process",
                            work_date=work_date,
                            brand_id=process.brand_id,
                            process_id=process.id,
                            minimum_staff=requirement.minimum_staff,
                            sequence=slot.sequence,
                        ))

    brand_ids = {process.brand_id for process in processes}
    for brand_id in brand_ids:
        rows = [row for row in staffing if row.brand_id == brand_id and row.process_id is None]
        dates = sorted(day for current_brand_id, day in open_brand_dates if current_brand_id == brand_id)
        for work_date in dates:
            requirement = _active_requirement(rows, work_date, process_id=None)
            if requirement is not None and requirement.minimum_staff > 0:
                brand_demands.append(StaffingDemand(
                    demand_id=f"brand-{brand_id}-{work_date.isoformat()}",
                    scope="brand",
                    work_date=work_date,
                    brand_id=brand_id,
                    process_id=None,
                    minimum_staff=requirement.minimum_staff,
                ))

    problem = MonthlyProblem(
        employee_ids=employee_ids,
        work_slots=tuple(slots),
        demands=tuple(process_demands + brand_demands),
    )
    return problem, employees, process_by_id, leaves


def _serialize_coverage(coverage) -> list[dict]:
    return [
        {
            "scope": row.scope,
            "work_date": row.work_date.isoformat(),
            "brand_id": row.brand_id,
            "process_id": row.process_id,
            "sequence": row.sequence,
            "required": row.required,
            "scheduled": row.scheduled,
            "uncovered": row.uncovered,
        }
        for row in coverage
    ]


def eligible_rows_for(rows_by_key, employee_id: int, process_id: int, work_date: date):
    return [row for row in rows_by_key.get((employee_id, process_id), [])
        if row.effective_from <= work_date and (row.effective_to is None or row.effective_to >= work_date)]


def generate_monthly_roster(
    session: Session,
    organization_id: int,
    pod_id: int,
    year: int,
    month: int,
    process_id: int | None = None,
    time_limit_seconds: int = 30,
    preserve_locks_from_roster_id: int | None = None,
) -> dict:
    start_date, end_date = _month_bounds(year, month)
    pod = session.scalar(select(Pod).where(Pod.id == pod_id, Pod.organization_id == organization_id))
    if pod is None:
        raise ResourceNotFound("Pod not found in this organization.")
    if process_id is not None:
        process = session.scalar(select(Process).where(
            Process.id == process_id,
            Process.organization_id == organization_id,
            Process.pod_id == pod_id,
        ))
        if process is None:
            raise ResourceNotFound("Process not found in this organization and pod.")

    locked_assignments: list[RosterWorkAssignment] = []
    if preserve_locks_from_roster_id is not None:
        from app.services.roster_validation import validate_roster
        source_roster = session.scalar(select(Roster).where(
            Roster.id == preserve_locks_from_roster_id,
            Roster.organization_id == organization_id,
            Roster.pod_id == pod_id,
            Roster.period_start == start_date,
            Roster.period_end == end_date,
        ).options(selectinload(Roster.entries).selectinload(RosterEntry.work_assignments)))
        if source_roster is None:
            raise ResourceNotFound("Source roster not found for this organization, Pod and month.")
        locked_assignments = [assignment for entry in source_roster.entries for assignment in entry.work_assignments
            if assignment.is_locked and (process_id is None or assignment.process_assignment.process_id == process_id)]
        validation = validate_roster(session, organization_id, source_roster.id)
        locked_ids = {row.id for row in locked_assignments}
        locked_keys = {
            (row.roster_entry.work_date, row.process_assignment.process_id, row.process_assignment.process.brand_id)
            for row in locked_assignments
        }
        lock_conflicts = [row for row in validation["conflicts"] if
            locked_ids.intersection(row["assignment_ids"])
            or (row["conflict_type"] == "process_coverage_gap" and
                any(row["work_date"] == day and row["process_id"] == pid for day, pid, _ in locked_keys))
            or (row["conflict_type"] == "brand_coverage_gap" and
                any(row["work_date"] == day and row["brand_id"] == bid for day, _, bid in locked_keys))]
        if lock_conflicts:
            raise LockedAssignmentConflict({
                "roster_id": source_roster.id,
                "valid": False,
                "conflict_count": len(lock_conflicts),
                "conflicts": lock_conflicts,
            })

    parameters = {
        "year": year,
        "month": month,
        "pod_id": pod_id,
        "process_id": process_id,
        "solver_time_limit_seconds": time_limit_seconds,
    }
    now = datetime.now(timezone.utc)
    run = GenerationRun(
        organization_id=organization_id,
        pod_id=pod_id,
        process_id=process_id,
        status="running",
        parameters=parameters,
        started_at=now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    def fail(status: str, solver_status: str, error: str, coverage: list[dict] | None = None) -> dict:
        run.status = "failed"
        run.error_summary = error[:2000]
        run.result_summary = {"solver_status": solver_status, "coverage": coverage or []}
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
        return {
            "success": False,
            "generation_run_id": run.id,
            "roster_id": None,
            "status": status,
            "solver_status": solver_status,
            "year": year,
            "month": month,
            "pod_id": pod_id,
            "assignments": [],
            "employee_statuses": [],
            "coverage": coverage or [],
            "error": error,
        }

    try:
        try:
            problem, employees, process_by_id, leaves = _make_problem(
                session, organization_id, pod_id, start_date, end_date, process_id, locked_assignments
            )
        except RosterGenerationError as exc:
            return fail("failed", "INVALID_CONFIGURATION", str(exc))
        open_days = {slot.work_date for slot in problem.work_slots}
        if not open_days:
            return fail("failed", "NO_OPERATING_DAYS", "No Process operating intervals are configured for this month and scope.")
        if not any(demand.minimum_staff > 0 for demand in problem.demands):
            return fail("failed", "NO_STAFFING_REQUIREMENTS", "No positive minimum staffing requirements apply to open Process dates.")
        solution = solve_monthly_roster(problem, time_limit_seconds=time_limit_seconds)
        if solution.status != "feasible":
            return fail(
                solution.status,
                solution.solver_status,
                solution.error or "Roster generation did not produce a feasible result.",
                _serialize_coverage(solution.coverage),
            )

        existing_version = session.scalar(select(func.max(Roster.version)).where(
            Roster.organization_id == organization_id,
            Roster.pod_id == pod_id,
            Roster.period_start == start_date,
            Roster.period_end == end_date,
        )) or 0
        roster = Roster(
            organization_id=organization_id,
            pod_id=pod_id,
            period_start=start_date,
            period_end=end_date,
            version=existing_version + 1,
            status="draft",
        )
        session.add(roster)
        session.flush()
        entry_by_employee_date = {}
        blocks_by_employee_date: dict[tuple[int, date], list[ScheduledBlock]] = {}
        for block in solution.blocks:
            blocks_by_employee_date.setdefault((block.employee_id, block.work_date), []).append(block)
        employee_by_id = {employee.id: employee for employee in employees}
        for offset in range((end_date - start_date).days + 1):
            work_date = start_date + timedelta(days=offset)
            for employee in employees:
                blocks = blocks_by_employee_date.get((employee.id, work_date), [])
                status = "working" if blocks else ("leave" if _leave_on_date(leaves, employee.id, work_date) else "day_off")
                entry = RosterEntry(
                    organization_id=organization_id,
                    pod_id=pod_id,
                    roster_id=roster.id,
                    employee_id=employee.id,
                    work_date=work_date,
                    status=status,
                )
                session.add(entry)
                entry_by_employee_date[(employee.id, work_date)] = entry
        session.flush()
        for (employee_id, work_date), blocks in blocks_by_employee_date.items():
            entry = entry_by_employee_date[(employee_id, work_date)]
            for block in blocks:
                process = process_by_id[block.process_id]
                session.add(RosterWorkAssignment(
                    organization_id=organization_id,
                    pod_id=pod_id,
                    roster_entry_id=entry.id,
                    employee_id=employee_id,
                    process_assignment_id=block.process_assignment_id,
                    starts_at=block.starts_at,
                    ends_at=block.ends_at,
                    work_label=process.name,
                    is_locked=block.locked,
                ))
        assignments_out = []
        for block in solution.blocks:
            process = process_by_id[block.process_id]
            assignments_out.append({
                "employee_id": block.employee_id,
                "employee_name": employee_by_id[block.employee_id].full_name,
                "process_id": process.id,
                "process_name": process.name,
                "brand_id": process.brand_id,
                "brand_name": process.brand.name,
                "process_assignment_id": block.process_assignment_id,
                "work_date": block.work_date,
                "starts_at": block.starts_at,
                "ends_at": block.ends_at,
            })
        statuses = [
            {
                "employee_id": employee.id,
                "employee_name": employee.full_name,
                "work_date": start_date + timedelta(days=offset),
                "status": entry_by_employee_date[(employee.id, start_date + timedelta(days=offset))].status,
            }
            for offset in range((end_date - start_date).days + 1)
            for employee in employees
        ]
        run.roster_id = roster.id
        run.status = "succeeded"
        run.result_summary = {
            "solver_status": solution.solver_status,
            "objective_value": solution.objective_value,
            "wall_time_seconds": solution.wall_time_seconds,
            "coverage": _serialize_coverage(solution.coverage),
        }
        run.error_summary = None
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
        return {
            "success": True,
            "generation_run_id": run.id,
            "roster_id": roster.id,
            "status": "succeeded",
            "solver_status": solution.solver_status,
            "year": year,
            "month": month,
            "pod_id": pod_id,
            "assignments": assignments_out,
            "employee_statuses": statuses,
            "coverage": _serialize_coverage(solution.coverage),
            "error": None,
        }
    except Exception as exc:
        session.rollback()
        # Keep the failed GenerationRun even when result persistence fails.
        failed_run = session.get(GenerationRun, run.id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_summary = str(exc)[:2000]
            failed_run.finished_at = datetime.now(timezone.utc)
            failed_run.result_summary = {"solver_status": "PERSISTENCE_ERROR"}
            session.commit()
        raise
