from datetime import date, datetime, timezone

from app.scheduling.monthly import (
    EligibleCandidate,
    MonthlyProblem,
    StaffingDemand,
    WorkSlot,
    solve_monthly_roster,
)


def slot(slot_id: str, process_id: int, starts_hour: int, ends_hour: int, candidates: tuple[EligibleCandidate, ...], sequence: int = 1) -> WorkSlot:
    work_date = date(2026, 4, 1)
    return WorkSlot(
        slot_id=slot_id,
        work_date=work_date,
        process_id=process_id,
        brand_id=process_id,
        sequence=sequence,
        starts_at=datetime(2026, 4, 1, starts_hour, tzinfo=timezone.utc),
        ends_at=datetime(2026, 4, 1, ends_hour, tzinfo=timezone.utc),
        candidates=candidates,
    )


def demand(process_id: int, slot_id: str, count: int = 1) -> StaffingDemand:
    return StaffingDemand(
        demand_id=f"demand-{slot_id}",
        scope="process",
        work_date=date(2026, 4, 1),
        brand_id=process_id,
        process_id=process_id,
        minimum_staff=count,
    )


def test_cp_sat_meets_hard_staffing_and_prefers_low_cost_candidate() -> None:
    candidates = (
        EligibleCandidate(employee_id=1, process_assignment_id=11, preference_cost=50),
        EligibleCandidate(employee_id=2, process_assignment_id=12, preference_cost=0),
    )
    problem = MonthlyProblem(
        employee_ids=(1, 2),
        work_slots=(slot("morning", 1, 9, 17, candidates),),
        demands=(demand(1, "morning"),),
    )

    result = solve_monthly_roster(problem, time_limit_seconds=2)

    assert result.status == "feasible"
    assert result.solver_status in {"OPTIMAL", "FEASIBLE"}
    assert [block.employee_id for block in result.blocks] == [2]
    assert result.coverage[0].uncovered == 0


def test_cp_sat_prevents_overlapping_work_blocks_for_an_employee() -> None:
    candidate = (EligibleCandidate(employee_id=1, process_assignment_id=5),)
    problem = MonthlyProblem(
        employee_ids=(1,),
        work_slots=(slot("a", 1, 9, 13, candidate), slot("b", 2, 12, 17, candidate)),
        demands=(demand(1, "a"), demand(2, "b")),
    )

    result = solve_monthly_roster(problem, time_limit_seconds=2)

    assert result.status == "infeasible"
    assert result.solver_status == "INFEASIBLE"
    assert sum(row.uncovered for row in result.coverage) == 1


def test_cp_sat_allows_multiple_nonoverlapping_processes_for_employee() -> None:
    candidate_a = (EligibleCandidate(employee_id=1, process_assignment_id=5),)
    candidate_b = (EligibleCandidate(employee_id=1, process_assignment_id=6),)
    problem = MonthlyProblem(
        employee_ids=(1,),
        work_slots=(slot("a", 1, 9, 12, candidate_a), slot("b", 2, 13, 17, candidate_b)),
        demands=(demand(1, "a"), demand(2, "b")),
    )

    result = solve_monthly_roster(problem, time_limit_seconds=2)

    assert result.status == "feasible"
    assert {block.process_id for block in result.blocks} == {1, 2}


def test_cp_sat_uses_fair_distribution_after_minimizing_extra_assignments() -> None:
    candidates = (
        EligibleCandidate(employee_id=1, process_assignment_id=5),
        EligibleCandidate(employee_id=2, process_assignment_id=6),
    )
    first = slot("first", 1, 9, 12, candidates)
    second = WorkSlot(
        slot_id="second",
        work_date=date(2026, 4, 2),
        process_id=1,
        brand_id=1,
        sequence=1,
        starts_at=datetime(2026, 4, 2, 9, tzinfo=timezone.utc),
        ends_at=datetime(2026, 4, 2, 12, tzinfo=timezone.utc),
        candidates=candidates,
    )
    second_demand = StaffingDemand("demand-second", "process", date(2026, 4, 2), 1, 1, 1)
    problem = MonthlyProblem(
        employee_ids=(1, 2),
        work_slots=(first, second),
        demands=(demand(1, "first"), second_demand),
    )

    result = solve_monthly_roster(problem, time_limit_seconds=2)

    assert result.status == "feasible"
    assert {block.employee_id for block in result.blocks} == {1, 2}


def test_cp_sat_returns_uncovered_demands_when_capacity_is_insufficient() -> None:
    candidate = (EligibleCandidate(employee_id=1, process_assignment_id=5),)
    problem = MonthlyProblem(
        employee_ids=(1,),
        work_slots=(slot("a", 1, 9, 17, candidate),),
        demands=(demand(1, "a", count=2),),
    )

    result = solve_monthly_roster(problem, time_limit_seconds=2)

    assert result.status == "infeasible"
    assert result.coverage[0].required == 2
    assert result.coverage[0].scheduled == 1
    assert result.coverage[0].uncovered == 1


def test_process_minimum_is_enforced_for_each_operating_sequence() -> None:
    candidate = (EligibleCandidate(employee_id=1, process_assignment_id=5),)
    morning = slot("morning", 1, 8, 12, candidate, sequence=1)
    evening = slot("evening", 1, 13, 17, candidate, sequence=2)
    demands = (
        StaffingDemand("morning-demand", "process", date(2026, 4, 1), 1, 1, 1, 1),
        StaffingDemand("evening-demand", "process", date(2026, 4, 1), 1, 1, 1, 2),
    )
    result = solve_monthly_roster(MonthlyProblem((1,), (morning, evening), demands), time_limit_seconds=2)
    assert result.status == "feasible"
    assert len(result.blocks) == 2
    assert {row.uncovered for row in result.coverage} == {0}
