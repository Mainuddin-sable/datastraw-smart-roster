"""Pure OR-Tools CP-SAT model for monthly employee-to-process work blocks."""

from dataclasses import dataclass
from datetime import date, datetime

from ortools.sat.python import cp_model


@dataclass(frozen=True)
class EligibleCandidate:
    employee_id: int
    process_assignment_id: int
    preference_cost: int = 0
    locked: bool = False


@dataclass(frozen=True)
class WorkSlot:
    slot_id: str
    work_date: date
    process_id: int
    brand_id: int
    sequence: int
    starts_at: datetime
    ends_at: datetime
    candidates: tuple[EligibleCandidate, ...]


@dataclass(frozen=True)
class StaffingDemand:
    demand_id: str
    scope: str
    work_date: date
    brand_id: int
    minimum_staff: int
    process_id: int | None = None
    sequence: int | None = None


@dataclass(frozen=True)
class MonthlyProblem:
    employee_ids: tuple[int, ...]
    work_slots: tuple[WorkSlot, ...]
    demands: tuple[StaffingDemand, ...]


@dataclass(frozen=True)
class ScheduledBlock:
    employee_id: int
    process_id: int
    brand_id: int
    process_assignment_id: int
    work_date: date
    starts_at: datetime
    ends_at: datetime
    locked: bool = False


@dataclass(frozen=True)
class Coverage:
    demand_id: str
    scope: str
    work_date: date
    brand_id: int
    process_id: int | None
    sequence: int | None
    required: int
    scheduled: int | None
    uncovered: int | None
    diagnosed: bool = True


@dataclass(frozen=True)
class MonthlySolution:
    status: str
    solver_status: str
    blocks: tuple[ScheduledBlock, ...] = ()
    coverage: tuple[Coverage, ...] = ()
    objective_value: float | None = None
    wall_time_seconds: float = 0.0
    error: str | None = None


def _minute(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Work slot datetimes must be timezone-aware")
    return int(value.timestamp() // 60)


def _build_and_solve(problem: MonthlyProblem, limit: float, soften_demands: bool):
    model = cp_model.CpModel()
    assignments = {}
    employee_intervals = {employee_id: [] for employee_id in problem.employee_ids}
    employee_load_terms = {employee_id: [] for employee_id in problem.employee_ids}
    demand_candidates = {demand.demand_id: {} for demand in problem.demands}
    preference_terms = []
    slot_candidates: dict[str, list[cp_model.IntVar]] = {}

    for slot in problem.work_slots:
        start = _minute(slot.starts_at)
        end = _minute(slot.ends_at)
        duration = end - start
        if duration <= 0:
            raise ValueError(f"Work slot {slot.slot_id} has a nonpositive duration")
        slot_candidates[slot.slot_id] = []
        for candidate in slot.candidates:
            key = (slot.slot_id, candidate.employee_id)
            if key in assignments:
                continue
            selected = model.new_bool_var(f"assign_{slot.slot_id}_{candidate.employee_id}")
            assignments[key] = selected
            if candidate.locked:
                model.add(selected == 1)
            slot_candidates[slot.slot_id].append(selected)
            interval = model.new_optional_interval_var(
                start, duration, end, selected, f"interval_{slot.slot_id}_{candidate.employee_id}"
            )
            employee_intervals.setdefault(candidate.employee_id, []).append(interval)
            employee_load_terms.setdefault(candidate.employee_id, []).append(duration * selected)
            if candidate.preference_cost > 0:
                preference_terms.append(candidate.preference_cost * selected)

    for intervals in employee_intervals.values():
        if intervals:
            model.add_no_overlap(intervals)

    for demand in problem.demands:
        selected_by_employee = {}
        for slot in problem.work_slots:
            if slot.work_date != demand.work_date or slot.brand_id != demand.brand_id:
                continue
            if demand.process_id is not None and slot.process_id != demand.process_id:
                continue
            if demand.sequence is not None and slot.sequence != demand.sequence:
                continue
            for candidate in slot.candidates:
                variable = assignments.get((slot.slot_id, candidate.employee_id))
                if variable is not None:
                    selected_by_employee.setdefault(candidate.employee_id, []).append(variable)
        demand_vars = []
        for employee_id, variables in selected_by_employee.items():
            worked = model.new_bool_var(f"demand_{demand.demand_id}_employee_{employee_id}")
            model.add_max_equality(worked, variables)
            demand_candidates[demand.demand_id][employee_id] = worked
            demand_vars.append(worked)
        if soften_demands:
            slack = model.new_int_var(0, demand.minimum_staff, f"uncovered_{demand.demand_id}")
            demand_candidates[demand.demand_id]["__slack__"] = slack
            model.add(sum(demand_vars) + slack >= demand.minimum_staff)
        else:
            model.add(sum(demand_vars) >= demand.minimum_staff)

    if soften_demands:
        model.minimize(
            sum(values["__slack__"] for values in demand_candidates.values() if "__slack__" in values)
        )
    else:
        total_candidate_minutes = sum(
            max(0, _minute(slot.ends_at) - _minute(slot.starts_at)) * len(slot.candidates)
            for slot in problem.work_slots
        )
        loads = []
        for employee_id in problem.employee_ids:
            load = model.new_int_var(0, max(total_candidate_minutes, 1), f"load_{employee_id}")
            model.add(load == sum(employee_load_terms.get(employee_id, [])))
            loads.append(load)
        spread = 0
        if loads:
            max_load = model.new_int_var(0, max(total_candidate_minutes, 1), "max_load")
            min_load = model.new_int_var(0, max(total_candidate_minutes, 1), "min_load")
            model.add_max_equality(max_load, loads)
            model.add_min_equality(min_load, loads)
            spread = max_load - min_load
        preference_bound = sum(
            max(0, candidate.preference_cost)
            for slot in problem.work_slots
            for candidate in slot.candidates
        )
        assignment_weight = (max(total_candidate_minutes, 1) + 1) * (preference_bound + 1)
        fairness_weight = preference_bound + 1
        model.minimize(
            sum(assignments.values()) * assignment_weight
            + spread * fairness_weight
            + sum(preference_terms)
        )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.05, limit)
    solver.parameters.num_search_workers = 8
    return solver, solver.solve(model), assignments, demand_candidates


def _coverage(problem: MonthlyProblem, solver: cp_model.CpSolver, demand_candidates: dict) -> tuple[Coverage, ...]:
    rows = []
    for demand in problem.demands:
        values = demand_candidates[demand.demand_id]
        scheduled = sum(solver.value(value) for key, value in values.items() if key != "__slack__")
        uncovered = solver.value(values["__slack__"]) if "__slack__" in values else max(0, demand.minimum_staff - scheduled)
        rows.append(Coverage(
            demand_id=demand.demand_id,
            scope=demand.scope,
            work_date=demand.work_date,
            brand_id=demand.brand_id,
            process_id=demand.process_id,
            sequence=demand.sequence,
            required=demand.minimum_staff,
            scheduled=scheduled,
            uncovered=uncovered,
        ))
    return tuple(rows)


def solve_monthly_roster(
    problem: MonthlyProblem,
    *,
    time_limit_seconds: float = 30.0,
    diagnostic_time_limit_seconds: float = 5.0,
) -> MonthlySolution:
    """Solve a prepared problem without importing FastAPI or database modules."""
    try:
        solver, status, assignments, demands = _build_and_solve(
            problem, time_limit_seconds, soften_demands=False
        )
    except ValueError as exc:
        return MonthlySolution("invalid", "MODEL_INVALID", error=str(exc))

    status_name = solver.status_name(status)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        slots = {slot.slot_id: slot for slot in problem.work_slots}
        blocks = []
        for (slot_id, employee_id), variable in assignments.items():
            if not solver.value(variable):
                continue
            slot = slots[slot_id]
            candidate = next(candidate for candidate in slot.candidates if candidate.employee_id == employee_id)
            blocks.append(ScheduledBlock(
                employee_id=employee_id,
                process_id=slot.process_id,
                brand_id=slot.brand_id,
                process_assignment_id=candidate.process_assignment_id,
                work_date=slot.work_date,
                starts_at=slot.starts_at,
                ends_at=slot.ends_at,
                locked=candidate.locked,
            ))
        return MonthlySolution(
            status="feasible",
            solver_status=status_name,
            blocks=tuple(sorted(blocks, key=lambda item: (item.work_date, item.starts_at, item.employee_id))),
            coverage=_coverage(problem, solver, demands),
            objective_value=solver.objective_value,
            wall_time_seconds=solver.wall_time,
        )

    if status == cp_model.INFEASIBLE:
        diagnostic, diagnostic_status, _, diagnostic_demands = _build_and_solve(
            problem, diagnostic_time_limit_seconds, soften_demands=True
        )
        if diagnostic_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            coverage = _coverage(problem, diagnostic, diagnostic_demands)
            error = "Hard staffing requirements cannot be met with the available eligible employees and constraints."
        else:
            coverage = tuple(Coverage(
                demand_id=demand.demand_id,
                scope=demand.scope,
                work_date=demand.work_date,
                brand_id=demand.brand_id,
                process_id=demand.process_id,
                sequence=demand.sequence,
                required=demand.minimum_staff,
                scheduled=None,
                uncovered=None,
                diagnosed=False,
            ) for demand in problem.demands if demand.minimum_staff > 0)
            error = (
                "Hard staffing requirements cannot be met; the diagnostic time limit expired "
                "before exact uncovered counts could be determined."
            )
        return MonthlySolution(
            status="infeasible",
            solver_status=status_name,
            coverage=tuple(row for row in coverage if row.uncovered is None or row.uncovered > 0),
            wall_time_seconds=solver.wall_time + diagnostic.wall_time,
            error=error,
        )

    if status == cp_model.MODEL_INVALID:
        return MonthlySolution(
            status="invalid",
            solver_status=status_name,
            wall_time_seconds=solver.wall_time,
            error="The CP-SAT model failed validation.",
        )

    return MonthlySolution(
        status="unknown",
        solver_status=status_name,
        wall_time_seconds=solver.wall_time,
        error="Solver reached its time limit without finding a feasible roster or proving infeasibility.",
    )
