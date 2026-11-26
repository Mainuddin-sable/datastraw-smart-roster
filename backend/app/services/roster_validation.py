"""Independent validation and controlled editing of persisted rosters."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Employee, EmployeeAvailability, EmployeeLeave, Process, ProcessAssignment, Roster,
    RosterEntry, RosterWorkAssignment, StaffingRequirement,
)
from app.services.calendars import get_effective_calendar
from app.services.organization_structure import ResourceNotFound
from app.services.roster_generation import _active_requirement, _as_utc, _availability_allows, _leave_overlaps


def _roster(session: Session, organization_id: int, roster_id: int) -> Roster:
    row = session.scalar(select(Roster).where(
        Roster.id == roster_id, Roster.organization_id == organization_id
    ).options(selectinload(Roster.entries).selectinload(RosterEntry.work_assignments)
              .selectinload(RosterWorkAssignment.process_assignment).selectinload(ProcessAssignment.process)))
    if row is None:
        raise ResourceNotFound("Roster not found in this organization.")
    return row


def _conflict(kind: str, work_date: date, message: str, *, employee_ids=(), brand_id=None,
              process_id=None, sequence=None, severity="error", assignment_ids=(), starts_at=None,
              ends_at=None) -> dict:
    return {
        "conflict_type": kind, "work_date": work_date, "employee_ids": sorted(set(employee_ids)),
        "brand_id": brand_id, "process_id": process_id, "sequence": sequence,
        "starts_at": starts_at, "ends_at": ends_at, "severity": severity,
        "message": message, "assignment_ids": sorted(set(assignment_ids)),
    }


def _employees_covering_interval(blocks, entry_by_id, assignment_process, valid_assignment_ids,
                                 *, work_date: date, process_id: int, starts_at: datetime,
                                 ends_at: datetime) -> dict[int, set[int]]:
    """Return assignment rows that continuously cover the complete interval."""
    intervals_by_employee: dict[int, list[tuple[datetime, datetime, int]]] = {}
    for block in blocks:
        if block.id not in valid_assignment_ids or assignment_process[block.id].id != process_id:
            continue
        if entry_by_id[block.roster_entry_id].work_date != work_date:
            continue
        block_start, block_end = _as_utc(block.starts_at), _as_utc(block.ends_at)
        clipped_start, clipped_end = max(block_start, starts_at), min(block_end, ends_at)
        if clipped_start < clipped_end:
            intervals_by_employee.setdefault(block.employee_id, []).append(
                (clipped_start, clipped_end, block.id)
            )

    covered: dict[int, set[int]] = {}
    for employee_id, intervals in intervals_by_employee.items():
        cursor = starts_at
        witness_ids = set()
        for block_start, block_end, block_id in sorted(intervals):
            if block_start > cursor:
                break
            if block_end > cursor:
                cursor = block_end
                witness_ids.add(block_id)
            if cursor >= ends_at:
                # The allocator below prevents these rows from being reused
                # across overlapping sequence demands. Coverage itself only
                # requires continuous presence through this interval.
                covered[employee_id] = witness_ids
                break
    return covered


def _allocate_interval_coverage(intervals: list[dict]) -> list[set[int]]:
    """Allocate work-block evidence once per employee across overlapping demands."""
    if not intervals:
        return []

    requirements = tuple(row["required"] for row in intervals)
    employee_options: dict[int, list[tuple[int, ...]]] = {}
    candidates = sorted({employee_id for row in intervals for employee_id in row["witnesses"]})
    for employee_id in candidates:
        eligible = [index for index, row in enumerate(intervals)
                    if employee_id in row["witnesses"]]
        options: list[tuple[int, ...]] = []

        def build_options(position: int, selected: tuple[int, ...], used_blocks: frozenset[int]) -> None:
            if position == len(eligible):
                options.append(selected)
                return
            index = eligible[position]
            build_options(position + 1, selected, used_blocks)
            row = intervals[index]
            block_ids = frozenset(row["witnesses"][employee_id])
            overlaps = any(
                row["starts_at"] < intervals[other]["ends_at"]
                and intervals[other]["starts_at"] < row["ends_at"]
                for other in selected
            )
            if not overlaps and not block_ids.intersection(used_blocks):
                build_options(position + 1, selected + (index,), used_blocks | block_ids)

        build_options(0, (), frozenset())
        employee_options[employee_id] = options

    # Counts are capped at requirements. Keeping one deterministic allocation
    # per count vector is sufficient to report whether every demand is met.
    allocations: dict[tuple[int, ...], tuple[frozenset[int], ...]] = {
        tuple(0 for _ in intervals): tuple(frozenset() for _ in intervals)
    }
    for employee_id in candidates:
        next_allocations = dict(allocations)
        for counts, assigned in allocations.items():
            for option in employee_options[employee_id]:
                if not option:
                    continue
                new_counts = list(counts)
                new_assigned = list(assigned)
                for index in option:
                    new_counts[index] = min(requirements[index], new_counts[index] + 1)
                    new_assigned[index] = new_assigned[index] | {employee_id}
                next_allocations.setdefault(tuple(new_counts), tuple(new_assigned))
        allocations = next_allocations

    best_counts = max(allocations, key=lambda counts: (sum(counts), counts))
    return [set(employee_ids) for employee_ids in allocations[best_counts]]


def validate_roster(session: Session, organization_id: int, roster_id: int) -> dict:
    roster = _roster(session, organization_id, roster_id)
    entries = roster.entries
    assignments = [assignment for entry in entries for assignment in entry.work_assignments]
    entry_by_id = {entry.id: entry for entry in entries}
    employee_ids = {entry.employee_id for entry in entries}
    employees = {row.id: row for row in session.scalars(select(Employee).where(
        Employee.organization_id == organization_id, Employee.pod_id == roster.pod_id,
        Employee.id.in_(employee_ids) if employee_ids else False,
    ))}
    availability = list(session.scalars(select(EmployeeAvailability).where(
        EmployeeAvailability.organization_id == organization_id, EmployeeAvailability.pod_id == roster.pod_id,
        EmployeeAvailability.employee_id.in_(employee_ids) if employee_ids else False,
    )))
    leaves = list(session.scalars(select(EmployeeLeave).where(
        EmployeeLeave.organization_id == organization_id, EmployeeLeave.pod_id == roster.pod_id,
        EmployeeLeave.status == "approved",
        EmployeeLeave.employee_id.in_(employee_ids) if employee_ids else False,
    )))
    conflicts = []
    valid_assignment_ids = {assignment.id for assignment in assignments}
    assignment_process = {}
    by_employee = {}
    for assignment in assignments:
        entry = entry_by_id[assignment.roster_entry_id]
        process_assignment = assignment.process_assignment
        process = process_assignment.process
        assignment_process[assignment.id] = process
        by_employee.setdefault(assignment.employee_id, []).append(assignment)
        work_date = entry.work_date
        tz = ZoneInfo(process.timezone)
        if assignment.employee_id not in employees or not employees[assignment.employee_id].is_active:
            valid_assignment_ids.discard(assignment.id)
            conflicts.append(_conflict("inactive_employee", work_date,
                "Employee is inactive but still has a roster assignment.", employee_ids=[assignment.employee_id],
                brand_id=process.brand_id, process_id=process.id, assignment_ids=[assignment.id]))
        if not (process_assignment.effective_from <= work_date and
                (process_assignment.effective_to is None or process_assignment.effective_to >= work_date)):
            valid_assignment_ids.discard(assignment.id)
            conflicts.append(_conflict("ineligible_process", work_date,
                "Employee process eligibility is not effective on this date.", employee_ids=[assignment.employee_id],
                brand_id=process.brand_id, process_id=process.id, assignment_ids=[assignment.id]))
        overlapping_leave = next((row for row in leaves if row.employee_id == assignment.employee_id
            and _as_utc(row.starts_at) < _as_utc(assignment.ends_at)
            and _as_utc(row.ends_at) > _as_utc(assignment.starts_at)), None)
        if overlapping_leave is not None:
            valid_assignment_ids.discard(assignment.id)
            is_comp_off = overlapping_leave.leave_type == "comp_off"
            conflicts.append(_conflict("approved_comp_off" if is_comp_off else "approved_leave", work_date,
                "Employee is assigned during approved comp-off." if is_comp_off else "Employee is assigned during approved leave.",
                employee_ids=[assignment.employee_id], brand_id=process.brand_id, process_id=process.id,
                assignment_ids=[assignment.id]))
        if not _availability_allows(availability, assignment.employee_id, work_date,
                _as_utc(assignment.starts_at), _as_utc(assignment.ends_at), tz):
            valid_assignment_ids.discard(assignment.id)
            current = [row for row in availability if row.employee_id == assignment.employee_id
                and row.day_of_week == work_date.weekday() and row.effective_from <= work_date
                and (row.effective_to is None or row.effective_to >= work_date)]
            kind = "weekly_off" if current and not any(row.is_available for row in current) else "unavailable"
            conflicts.append(_conflict(kind, work_date, "Employee availability does not allow this assignment.",
                employee_ids=[assignment.employee_id], brand_id=process.brand_id, process_id=process.id,
                assignment_ids=[assignment.id]))
        calendar_rows = get_effective_calendar(session, organization_id, process.id, work_date, work_date)
        matched = False
        for rule in calendar_rows:
            if not rule["is_open"]:
                continue
            start = datetime.combine(work_date, rule["opens_at"]).replace(tzinfo=tz).astimezone(timezone.utc)
            end_date = work_date + (timedelta(days=1) if rule["closes_at"] <= rule["opens_at"] else timedelta())
            end = datetime.combine(end_date, rule["closes_at"]).replace(tzinfo=tz).astimezone(timezone.utc)
            if _as_utc(assignment.starts_at) >= start and _as_utc(assignment.ends_at) <= end:
                matched = True
                break
        if not matched:
            valid_assignment_ids.discard(assignment.id)
            conflicts.append(_conflict("outside_operating_calendar", work_date,
                "Assignment is outside every open operating interval for this Process.",
                employee_ids=[assignment.employee_id], brand_id=process.brand_id, process_id=process.id,
                assignment_ids=[assignment.id]))

    for employee_id, blocks in by_employee.items():
        blocks.sort(key=lambda row: _as_utc(row.starts_at))
        for index, first in enumerate(blocks):
            for second in blocks[index + 1:]:
                if _as_utc(second.starts_at) >= _as_utc(first.ends_at):
                    break
                first_process, second_process = assignment_process[first.id], assignment_process[second.id]
                valid_assignment_ids.discard(first.id)
                valid_assignment_ids.discard(second.id)
                kind = "duplicate_assignment" if (
                    _as_utc(first.starts_at) == _as_utc(second.starts_at)
                    and _as_utc(first.ends_at) == _as_utc(second.ends_at)
                    and first_process.id == second_process.id
                ) else "overlapping_assignments"
                conflicts.append(_conflict(kind, entry_by_id[first.roster_entry_id].work_date,
                    "Employee has duplicate or overlapping work assignments.", employee_ids=[employee_id],
                    brand_id=first_process.brand_id, process_id=first_process.id,
                    assignment_ids=[first.id, second.id]))

    process_rows = list(session.scalars(select(StaffingRequirement).where(
        StaffingRequirement.organization_id == organization_id, StaffingRequirement.pod_id == roster.pod_id)))
    processes = list(session.scalars(select(Process).where(
        Process.organization_id == organization_id, Process.pod_id == roster.pod_id)))
    brand_open_dates: set[tuple[int, date]] = set()
    brand_covered_employees: dict[tuple[int, date], set[int]] = {}
    for process in processes:
        rules = get_effective_calendar(session, organization_id, process.id, roster.period_start, roster.period_end)
        missing_requirement_dates: set[date] = set()
        intervals_by_date: dict[date, list[dict]] = {}
        for rule in rules:
            if not rule["is_open"]:
                continue
            day = rule["operating_date"]
            brand_open_dates.add((process.brand_id, day))
            requirement = _active_requirement(process_rows, day, process_id=process.id)
            start = datetime.combine(day, rule["opens_at"]).replace(tzinfo=ZoneInfo(process.timezone)).astimezone(timezone.utc)
            end_date = day + (timedelta(days=1) if rule["closes_at"] <= rule["opens_at"] else timedelta())
            end = datetime.combine(end_date, rule["closes_at"]).replace(tzinfo=ZoneInfo(process.timezone)).astimezone(timezone.utc)
            witnesses = _employees_covering_interval(
                assignments, entry_by_id, assignment_process, valid_assignment_ids,
                work_date=day, process_id=process.id, starts_at=start, ends_at=end,
            )
            brand_covered_employees.setdefault((process.brand_id, day), set()).update(witnesses)
            if requirement is None:
                if day not in missing_requirement_dates:
                    conflicts.append(_conflict(
                        "missing_staffing_requirement", day,
                        f"Open Process {process.name!r} (ID {process.id}) has no applicable staffing requirement on {day.isoformat()}.",
                        brand_id=process.brand_id, process_id=process.id,
                    ))
                    missing_requirement_dates.add(day)
                continue
            if requirement.minimum_staff <= 0:
                continue
            intervals_by_date.setdefault(day, []).append({
                "sequence": rule["sequence"], "starts_at": start, "ends_at": end,
                "required": requirement.minimum_staff, "witnesses": witnesses,
            })

        for day, intervals in intervals_by_date.items():
            allocated = _allocate_interval_coverage(intervals)
            for interval, covered in zip(intervals, allocated):
                if len(covered) < interval["required"]:
                    sequence = interval["sequence"]
                    start, end = interval["starts_at"], interval["ends_at"]
                    conflicts.append(_conflict(
                        "process_coverage_gap", day,
                        f"Process requires {interval['required']} employee(s) for the full operating interval; {len(covered)} cover it.",
                        employee_ids=covered, brand_id=process.brand_id, process_id=process.id,
                        sequence=sequence, starts_at=start, ends_at=end,
                    ))

    # Brand minima count unique employees across all process assignments for that Brand/date.
    from app.models import Brand
    brands = list(session.scalars(select(Brand).where(Brand.organization_id == organization_id, Brand.pod_id == roster.pod_id)))
    for brand in brands:
        days = sorted(day for brand_id, day in brand_open_dates if brand_id == brand.id)
        for day in days:
            requirement = _active_requirement(
                [row for row in process_rows if row.brand_id == brand.id and row.process_id is None],
                day, process_id=None,
            )
            if requirement is None or requirement.brand_id != brand.id or requirement.minimum_staff <= 0:
                continue
            covered = brand_covered_employees.get((brand.id, day), set())
            if len(covered) < requirement.minimum_staff:
                conflicts.append(_conflict("brand_coverage_gap", day,
                    f"Brand requires {requirement.minimum_staff} employee(s); {len(covered)} are assigned.",
                    employee_ids=covered, brand_id=brand.id))

    for conflict in conflicts:
        conflict["replacement_employee_ids"] = _replacement_candidates(
            session, roster, conflict, assignments, entry_by_id, availability, leaves
        ) if conflict["conflict_type"] in {"process_coverage_gap", "brand_coverage_gap"} else []
    return {"roster_id": roster.id, "valid": not conflicts, "conflicts": conflicts,
            "conflict_count": len(conflicts)}


def _replacement_candidates(session, roster, conflict, assignments, entry_by_id, availability, leaves):
    day = conflict["work_date"]
    processes = list(session.scalars(select(Process).where(
        Process.organization_id == roster.organization_id, Process.pod_id == roster.pod_id,
        Process.brand_id == conflict["brand_id"],
        Process.id == conflict["process_id"] if conflict["process_id"] else True,
    )))
    employees = list(session.scalars(select(Employee).where(
        Employee.organization_id == roster.organization_id, Employee.pod_id == roster.pod_id,
        Employee.is_active.is_(True),
    )))
    suggested = set()
    for process in processes:
        tz = ZoneInfo(process.timezone)
        for rule in get_effective_calendar(session, roster.organization_id, process.id, day, day):
            if not rule["is_open"]:
                continue
            start = datetime.combine(day, rule["opens_at"]).replace(tzinfo=tz).astimezone(timezone.utc)
            end_date = day + (timedelta(days=1) if rule["closes_at"] <= rule["opens_at"] else timedelta())
            end = datetime.combine(end_date, rule["closes_at"]).replace(tzinfo=tz).astimezone(timezone.utc)
            expected_start = conflict.get("starts_at")
            expected_end = conflict.get("ends_at")
            if expected_start is not None and _as_utc(expected_start) != start:
                continue
            if expected_end is not None and _as_utc(expected_end) != end:
                continue
            for employee in employees:
                eligible = session.scalar(select(ProcessAssignment.id).where(
                    ProcessAssignment.organization_id == roster.organization_id,
                    ProcessAssignment.pod_id == roster.pod_id,
                    ProcessAssignment.employee_id == employee.id,
                    ProcessAssignment.process_id == process.id,
                    ProcessAssignment.effective_from <= day,
                    (ProcessAssignment.effective_to.is_(None) | (ProcessAssignment.effective_to >= day)),
                ).limit(1))
                if eligible is None or not _availability_allows(availability, employee.id, day, start, end, tz):
                    continue
                if _leave_overlaps(leaves, employee.id, start, end):
                    continue
                conflicts_with_existing = False
                for block in assignments:
                    if block.employee_id != employee.id:
                        continue
                    if entry_by_id[block.roster_entry_id].work_date != day:
                        continue
                    if _as_utc(block.starts_at) < end and _as_utc(block.ends_at) > start:
                        conflicts_with_existing = True
                        break
                if not conflicts_with_existing:
                    suggested.add(employee.id)
    return sorted(suggested)


def get_assignment_candidates(session: Session, organization_id: int, roster_id: int,
                              assignment_id: int) -> dict:
    """Return same-Pod employees who can take this block under current hard rules."""
    roster = _roster(session, organization_id, roster_id)
    entries = roster.entries
    entry_by_id = {entry.id: entry for entry in entries}
    assignments = [item for entry in entries for item in entry.work_assignments]
    target = next((item for item in assignments if item.id == assignment_id), None)
    if target is None:
        raise ResourceNotFound("Roster assignment not found in this roster.")

    assignment_process = {item.id: item.process_assignment.process for item in assignments}
    process = assignment_process[target.id]
    work_date = entry_by_id[target.roster_entry_id].work_date
    availability = list(session.scalars(select(EmployeeAvailability).where(
        EmployeeAvailability.organization_id == organization_id,
        EmployeeAvailability.pod_id == roster.pod_id,
    )))
    leaves = list(session.scalars(select(EmployeeLeave).where(
        EmployeeLeave.organization_id == organization_id,
        EmployeeLeave.pod_id == roster.pod_id,
        EmployeeLeave.status == "approved",
    )))
    candidates = _replacement_candidates(
        session,
        roster,
        {
            "work_date": work_date,
            "brand_id": process.brand_id,
            "process_id": process.id,
            "starts_at": target.starts_at,
            "ends_at": target.ends_at,
        },
        [item for item in assignments if item.id != target.id],
        entry_by_id,
        availability,
        leaves,
    )
    return {
        "roster_id": roster.id,
        "assignment_id": target.id,
        "candidate_employee_ids": [employee_id for employee_id in candidates
                                   if employee_id != target.employee_id],
    }


def edit_assignment(session: Session, organization_id: int, roster_id: int, assignment_id: int,
                    employee_id: int, process_assignment_id: int) -> dict:
    roster = _roster(session, organization_id, roster_id)
    assignment = next((item for entry in roster.entries for item in entry.work_assignments if item.id == assignment_id), None)
    if assignment is None:
        raise ResourceNotFound("Roster assignment not found in this roster.")
    if assignment.is_locked:
        raise ValueError("Unlock this assignment before editing it.")
    entry = next(row for row in roster.entries if row.id == assignment.roster_entry_id)
    target = session.scalar(select(ProcessAssignment).where(
        ProcessAssignment.id == process_assignment_id,
        ProcessAssignment.organization_id == organization_id,
        ProcessAssignment.pod_id == roster.pod_id,
        ProcessAssignment.employee_id == employee_id,
        ProcessAssignment.effective_from <= entry.work_date,
        (ProcessAssignment.effective_to.is_(None) | (ProcessAssignment.effective_to >= entry.work_date)),
    ))
    if target is None:
        raise ValueError("The selected Employee is not eligible for this Process on the roster date.")
    original = assignment.employee_id, assignment.process_assignment_id
    old_entry_id = assignment.roster_entry_id
    assignment.employee_id = employee_id
    assignment.process_assignment = target
    assignment.roster_entry_id = session.scalar(select(RosterEntry.id).where(
        RosterEntry.roster_id == roster_id, RosterEntry.employee_id == employee_id,
        RosterEntry.work_date == entry.work_date))
    if assignment.roster_entry_id is None:
        session.rollback()
        assignment.employee_id, assignment.process_assignment_id = original
        raise ValueError("Selected Employee has no daily entry in this roster.")
    try:
        session.flush()
        checked = validate_roster(session, organization_id, roster_id)
        if not checked["valid"]:
            session.rollback()
            raise ValueError("Manual edit violates hard roster constraints: " + "; ".join(
                conflict["message"] for conflict in checked["conflicts"][:3]))
        old_entry = session.get(RosterEntry, old_entry_id)
        new_entry = session.get(RosterEntry, assignment.roster_entry_id)
        if old_entry is not None:
            session.expire(old_entry, ["work_assignments"])
        if old_entry is not None and not old_entry.work_assignments:
            old_entry.status = "day_off"
        if new_entry is not None:
            new_entry.status = "working"
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"assignment_id": assignment_id, "roster_id": roster_id, "employee_id": employee_id,
            "process_assignment_id": process_assignment_id, "work_date": entry.work_date}


def set_assignment_lock(session: Session, organization_id: int, roster_id: int, assignment_id: int, locked: bool) -> dict:
    roster = _roster(session, organization_id, roster_id)
    assignment = next((item for entry in roster.entries for item in entry.work_assignments if item.id == assignment_id), None)
    if assignment is None:
        raise ResourceNotFound("Roster assignment not found in this roster.")
    if locked:
        report = validate_roster(session, organization_id, roster_id)
        own_conflicts = [c for c in report["conflicts"] if assignment_id in c["assignment_ids"]]
        if own_conflicts:
            raise ValueError("Cannot lock an invalid assignment: " + "; ".join(c["message"] for c in own_conflicts))
    assignment.is_locked = locked
    session.commit()
    return {"assignment_id": assignment.id, "roster_id": roster_id, "is_locked": assignment.is_locked}
