from collections.abc import Iterator
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.db.base import Base
from app.main import app
from auth_support import TEST_AUTH_HEADERS
from app.models import EmployeeAvailability, EmployeeLeave, GenerationRun, ProcessAssignment, Roster, RosterEntry, RosterWorkAssignment, StaffingRequirement
from app.services.roster_validation import _employees_covering_interval


@pytest.fixture
def environment() -> Iterator[tuple[TestClient, object]]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            yield client, engine
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def setup_monthly_scope(client: TestClient, employee_count: int = 1) -> tuple[dict, dict, dict, dict, list[dict]]:
    org = client.post("/organizations", json={"name": "Monthly Org"}).json()
    pod = client.post(f"/organizations/{org['id']}/pods", json={"name": "Monthly Pod"}).json()
    brand = client.post(
        f"/organizations/{org['id']}/brands", json={"pod_id": pod["id"], "name": "Monthly Brand"}
    ).json()
    process = client.post(
        f"/organizations/{org['id']}/processes", json={"brand_id": brand["id"], "name": "Monthly Process"}
    ).json()
    rules = [
        {
            "day_of_week": weekday,
            "sequence": 1,
            "is_open": True,
            "opens_at": "09:00:00",
            "closes_at": "17:00:00",
            "effective_from": "2026-01-01",
        }
        for weekday in range(7)
    ]
    calendar = client.put(
        f"/organizations/{org['id']}/processes/{process['id']}/calendar",
        json={"timezone": "UTC", "rules": rules},
    )
    assert calendar.status_code == 200
    staffing = client.post(
        f"/organizations/{org['id']}/staffing-requirements",
        json={
            "brand_id": brand["id"],
            "process_id": process["id"],
            "minimum_staff": 1,
            "effective_from": "2026-01-01",
        },
    )
    assert staffing.status_code == 201
    employees = []
    for index in range(employee_count):
        employee_response = client.post(
            f"/organizations/{org['id']}/employees",
            json={
                "pod_id": pod["id"],
                "full_name": f"Employee {index + 1}",
                "email": f"employee{index + 1}@example.test",
            },
        )
        assert employee_response.status_code == 201
        employee = employee_response.json()
        assignment = client.post(
            f"/organizations/{org['id']}/employees/{employee['id']}/process-assignments",
            json={"process_id": process["id"], "effective_from": "2026-01-01"},
        )
        assert assignment.status_code == 201
        employees.append(employee)
    return org, pod, brand, process, employees


def _coverage_witnesses(intervals: list[tuple[int, int]], required_start: int = 12,
                        required_end: int = 16) -> dict[int, set[int]]:
    blocks = [SimpleNamespace(
        id=index + 1, roster_entry_id=1, employee_id=7,
        starts_at=datetime(2026, 4, 1, start, tzinfo=timezone.utc),
        ends_at=datetime(2026, 4, 1, end, tzinfo=timezone.utc),
    ) for index, (start, end) in enumerate(intervals)]
    process = SimpleNamespace(id=4)
    return _employees_covering_interval(
        blocks,
        {1: SimpleNamespace(work_date=date(2026, 4, 1))},
        {block.id: process for block in blocks},
        {block.id for block in blocks},
        work_date=date(2026, 4, 1), process_id=4,
        starts_at=datetime(2026, 4, 1, required_start, tzinfo=timezone.utc),
        ends_at=datetime(2026, 4, 1, required_end, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    "block_interval",
    [(12, 16), (9, 17), (9, 16), (12, 17)],
    ids=["exact", "extends-both-boundaries", "extends-before", "extends-after"],
)
def test_interval_coverage_accepts_full_exact_and_overhanging_blocks(block_interval) -> None:
    assert _coverage_witnesses([block_interval]) == {7: {1}}


@pytest.mark.parametrize(
    "block_intervals",
    [[(11, 15)], [(15, 17)]],
    ids=["partial-before-end", "partial-after-start"],
)
def test_interval_coverage_rejects_partial_blocks(block_intervals) -> None:
    assert _coverage_witnesses(block_intervals) == {}


def test_interval_coverage_merges_adjacent_blocks_but_rejects_a_gap() -> None:
    assert _coverage_witnesses([(12, 14), (14, 16)]) == {7: {1, 2}}
    assert _coverage_witnesses([(12, 13), (14, 16)]) == {}


def test_monthly_generation_uses_cp_sat_and_persists_roster_graph(environment) -> None:
    client, engine = environment
    org, pod, brand, process, employees = setup_monthly_scope(client)

    response = client.post(
        f"/organizations/{org['id']}/rosters/generate",
        json={"pod_id": pod["id"], "year": 2026, "month": 4, "solver_time_limit_seconds": 5},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["success"] is True
    assert result["status"] == "succeeded"
    assert result["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert result["roster_id"] is not None
    assert len(result["assignments"]) == 30
    assert len(result["employee_statuses"]) == 30
    assert all(row["status"] == "working" for row in result["employee_statuses"])
    assert all(row["uncovered"] == 0 for row in result["coverage"])
    assert result["conflicts"] == []
    assert all(row["process_id"] == process["id"] and row["brand_id"] == brand["id"] for row in result["assignments"])
    persisted = client.get(
        f"/organizations/{org['id']}/rosters/lookup",
        params={"pod_id": pod["id"], "year": 2026, "month": 4},
    )
    assert persisted.status_code == 200
    assert persisted.json()["roster_id"] == result["roster_id"]
    assert persisted.json()["assignments"][0]["assignment_id"] == result["assignments"][0]["assignment_id"]

    with Session(engine) as session:
        roster = session.get(Roster, result["roster_id"])
        run = session.get(GenerationRun, result["generation_run_id"])
        assert roster is not None and roster.version == 1
        assert run is not None and run.status == "succeeded" and run.roster_id == roster.id
        assert len(list(session.scalars(select(RosterEntry).where(RosterEntry.roster_id == roster.id)))) == 30
        assert len(list(session.scalars(select(RosterWorkAssignment).where(RosterWorkAssignment.roster_entry_id.in_(
            select(RosterEntry.id).where(RosterEntry.roster_id == roster.id)
        ))))) == 30


def test_infeasible_month_returns_uncovered_days_and_does_not_save_roster(environment) -> None:
    client, engine = environment
    org, pod, _, process, employees = setup_monthly_scope(client)
    unavailable = client.post(
        f"/organizations/{org['id']}/employees/{employees[0]['id']}/availability",
        json={
            "day_of_week": 6,
            "sequence": 1,
            "is_available": False,
            "effective_from": "2026-01-01",
        },
    )
    assert unavailable.status_code == 201

    response = client.post(
        f"/organizations/{org['id']}/rosters/generate",
        json={"pod_id": pod["id"], "year": 2026, "month": 4, "process_id": process["id"], "solver_time_limit_seconds": 5},
    )

    assert response.status_code == 409
    result = response.json()
    assert result["success"] is False
    assert result["status"] == "infeasible"
    assert result["roster_id"] is None
    assert result["coverage"]
    assert all(row["uncovered"] > 0 for row in result["coverage"])
    assert any(row["work_date"].endswith("05") for row in result["coverage"])

    with Session(engine) as session:
        assert session.get(GenerationRun, result["generation_run_id"]).status == "failed"
        assert session.scalar(select(Roster.id).where(Roster.pod_id == pod["id"])) is None


def test_monthly_generation_respects_approved_leave_and_returns_openapi(environment) -> None:
    client, engine = environment
    org, pod, _, process, employees = setup_monthly_scope(client, employee_count=2)
    with Session(engine) as session:
        session.add(EmployeeLeave(
            organization_id=org["id"],
            pod_id=pod["id"],
            employee_id=employees[0]["id"],
            starts_at=datetime(2026, 4, 7, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 4, 8, 0, tzinfo=timezone.utc),
            leave_type="annual",
            status="approved",
        ))
        session.commit()

    response = client.post(
        f"/organizations/{org['id']}/rosters/generate",
        json={"pod_id": pod["id"], "year": 2026, "month": 4, "process_id": process["id"], "solver_time_limit_seconds": 5},
    )
    assert response.status_code == 201
    april_seven = [row for row in response.json()["assignments"] if row["work_date"] == "2026-04-07"]
    assert len(april_seven) == 1
    assert april_seven[0]["employee_id"] == employees[1]["id"]
    assert "/organizations/{organization_id}/rosters/generate" in client.get("/openapi.json").json()["paths"]


def test_approved_comp_off_blocks_generation_and_creates_a_replacement_coverage_gap(environment) -> None:
    client, _ = environment
    org, pod, _, process, employees = setup_monthly_scope(client, employee_count=2)
    original = _generate(client, org, pod).json()
    target = next(row for row in original["assignments"] if row["work_date"] == "2026-04-01")
    endpoint = f"/organizations/{org['id']}/leave-requests"
    requested = client.post(endpoint, json={
        "employee_id": target["employee_id"], "starts_at": target["starts_at"],
        "ends_at": target["ends_at"], "leave_type": "comp_off", "reason": "Approved compensatory day off",
    })
    assert requested.status_code == 201, requested.text
    approved = client.patch(f"{endpoint}/{requested.json()['id']}", json={"status": "approved"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    old_report = client.post(f"/organizations/{org['id']}/rosters/{original['roster_id']}/revalidate").json()
    comp_off = next(row for row in old_report["conflicts"] if row["work_date"] == "2026-04-01"
                    and row["conflict_type"] == "approved_comp_off")
    gap = next(row for row in old_report["conflicts"] if row["work_date"] == "2026-04-01"
               and row["conflict_type"] == "process_coverage_gap")
    replacement = next(employee["id"] for employee in employees if employee["id"] != target["employee_id"])
    assert comp_off["assignment_ids"] == [target["assignment_id"]]
    assert gap["replacement_employee_ids"] == [replacement]

    regenerated = _generate(client, org, pod).json()
    april_first = [row for row in regenerated["assignments"] if row["work_date"] == "2026-04-01"]
    assert len(april_first) == 1
    assert april_first[0]["employee_id"] == replacement
    original_status = next(row for row in regenerated["employee_statuses"]
                           if row["employee_id"] == target["employee_id"] and row["work_date"] == "2026-04-01")
    assert original_status["status"] == "comp_off"
    assert process["id"] == april_first[0]["process_id"]


def test_leave_request_lifecycle_updates_saved_roster_validation(environment) -> None:
    client, _ = environment
    org, pod, _, _, employees = setup_monthly_scope(client, employee_count=2)
    generated = _generate(client, org, pod).json()
    target = next(row for row in generated["assignments"] if row["work_date"] == "2026-04-01")
    endpoint = f"/organizations/{org['id']}/leave-requests"
    payload = {
        "employee_id": target["employee_id"],
        "starts_at": target["starts_at"],
        "ends_at": target["ends_at"],
        "leave_type": "annual",
        "reason": "Planned time away",
    }

    requested = client.post(endpoint, json=payload)
    assert requested.status_code == 201, requested.text
    leave = requested.json()
    assert leave["status"] == "requested"
    assert leave["employee_id"] == target["employee_id"]
    assert datetime.fromisoformat(leave["starts_at"]).utcoffset() == timezone.utc.utcoffset(None)
    assert datetime.fromisoformat(leave["ends_at"]).utcoffset() == timezone.utc.utcoffset(None)
    assert len(client.get(f"{endpoint}?status=requested").json()) == 1
    assert client.post(endpoint, json=payload).status_code == 409

    before_approval = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/revalidate"
    ).json()
    assert before_approval["valid"] is True

    approved = client.patch(f"{endpoint}/{leave['id']}", json={"status": "approved"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    after_approval = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/revalidate"
    ).json()
    assert any(row["conflict_type"] == "approved_leave" for row in after_approval["conflicts"])
    gap = next(row for row in after_approval["conflicts"]
               if row["conflict_type"] == "process_coverage_gap" and row["work_date"] == "2026-04-01")
    assert any(employee["id"] != target["employee_id"] for employee in employees
               if employee["id"] in gap["replacement_employee_ids"])
    assert client.patch(f"{endpoint}/{leave['id']}", json={"status": "rejected"}).status_code == 409

    cancelled = client.patch(f"{endpoint}/{leave['id']}", json={"status": "cancelled"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    after_cancellation = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/revalidate"
    ).json()
    assert after_cancellation["valid"] is True

    assert client.post(endpoint, json={**payload, "starts_at": "2026-04-01T09:00:00"}).status_code == 422
    assert client.post(endpoint, json={**payload, "ends_at": payload["starts_at"]}).status_code == 422
    assert client.patch(f"{endpoint}/999999", json={"status": "approved"}).status_code == 404
    non_utc = client.post(endpoint, json={
        **payload,
        "starts_at": "2026-04-02T09:00:00+05:30",
        "ends_at": "2026-04-02T10:00:00+05:30",
    })
    assert non_utc.status_code == 201
    assert datetime.fromisoformat(non_utc.json()["starts_at"]) == datetime(2026, 4, 2, 3, 30, tzinfo=timezone.utc)
    assert datetime.fromisoformat(non_utc.json()["ends_at"]) == datetime(2026, 4, 2, 4, 30, tzinfo=timezone.utc)


def test_leave_requests_are_scoped_and_only_active_employees_can_request(environment) -> None:
    client, _ = environment
    org, _, _, _, employees = setup_monthly_scope(client)
    foreign_org = client.post("/organizations", json={"name": "Foreign leave org"}).json()
    foreign_pod = client.post(
        f"/organizations/{foreign_org['id']}/pods", json={"name": "Foreign pod"}
    ).json()
    foreign_employee = client.post(
        f"/organizations/{foreign_org['id']}/employees",
        json={"pod_id": foreign_pod["id"], "full_name": "Foreign Employee",
              "email": "foreign@example.test"},
    ).json()
    endpoint = f"/organizations/{org['id']}/leave-requests"
    payload = {
        "employee_id": employees[0]["id"], "starts_at": "2026-04-01T09:00:00Z",
        "ends_at": "2026-04-01T17:00:00Z", "leave_type": "annual",
    }
    inactive = client.put(
        f"/organizations/{org['id']}/employees/{employees[0]['id']}",
        json={"full_name": employees[0]["full_name"], "email": employees[0]["email"],
              "role": employees[0]["role"], "is_active": False},
    )
    assert inactive.status_code == 200
    assert client.post(endpoint, json=payload).status_code == 409
    assert client.post(endpoint, json={**payload, "employee_id": foreign_employee["id"]}).status_code == 404
    assert client.get("/organizations/999999/leave-requests").status_code == 404


def test_monthly_generation_respects_effective_process_eligibility(environment) -> None:
    client, engine = environment
    org, pod, _, process, employees = setup_monthly_scope(client)
    eligibility = client.get(
        f"/organizations/{org['id']}/employees/{employees[0]['id']}/process-assignments"
    ).json()[0]
    updated = client.put(
        f"/organizations/{org['id']}/employees/{employees[0]['id']}/process-assignments/{eligibility['id']}",
        json={"process_id": process["id"], "effective_from": "2026-04-02"},
    )
    assert updated.status_code == 200

    response = client.post(
        f"/organizations/{org['id']}/rosters/generate",
        json={"pod_id": pod["id"], "year": 2026, "month": 4, "process_id": process["id"], "solver_time_limit_seconds": 5},
    )

    assert response.status_code == 409
    result = response.json()
    assert any(row["work_date"] == "2026-04-01" and row["uncovered"] == 1 for row in result["coverage"])
    with Session(engine) as session:
        assert session.scalar(select(Roster.id).where(Roster.pod_id == pod["id"])) is None


def test_monthly_generation_excludes_inactive_employees(environment) -> None:
    client, _ = environment
    org, pod, _, process, employees = setup_monthly_scope(client)
    response = client.put(
        f"/organizations/{org['id']}/employees/{employees[0]['id']}",
        json={
            "full_name": employees[0]["full_name"],
            "email": employees[0]["email"],
            "role": employees[0]["role"],
            "is_active": False,
        },
    )
    assert response.status_code == 200

    generated = client.post(
        f"/organizations/{org['id']}/rosters/generate",
        json={"pod_id": pod["id"], "year": 2026, "month": 4, "process_id": process["id"], "solver_time_limit_seconds": 5},
    )

    assert generated.status_code == 409
    assert generated.json()["status"] == "infeasible"


def _generate(client, org, pod, *, process_id=None, preserve=None):
    payload = {"pod_id": pod["id"], "year": 2026, "month": 4, "solver_time_limit_seconds": 5}
    if process_id is not None:
        payload["process_id"] = process_id
    if preserve is not None:
        payload["preserve_locks_from_roster_id"] = preserve
    return client.post(f"/organizations/{org['id']}/rosters/generate", json=payload)


def _add_daily_process_sequence(client, org, process, opens_at: str, closes_at: str) -> None:
    rules = [{
        "day_of_week": weekday,
        "sequence": 2,
        "is_open": True,
        "opens_at": opens_at,
        "closes_at": closes_at,
        "effective_from": "2026-01-01",
    } for weekday in range(7)]
    response = client.put(
        f"/organizations/{org['id']}/processes/{process['id']}/calendar",
        json={"timezone": "UTC", "rules": rules},
    )
    assert response.status_code == 200, response.text


def test_saved_roster_validation_reports_leave_and_weekly_off(environment) -> None:
    client, engine = environment
    org, pod, _, _, employees = setup_monthly_scope(client)
    generated = _generate(client, org, pod).json()
    with Session(engine) as session:
        session.add(EmployeeLeave(
            organization_id=org["id"], pod_id=pod["id"], employee_id=employees[0]["id"],
            starts_at=datetime(2026, 4, 7, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 4, 8, 0, tzinfo=timezone.utc), leave_type="annual", status="approved",
        ))
        session.add(EmployeeAvailability(
            organization_id=org["id"], pod_id=pod["id"], employee_id=employees[0]["id"],
            day_of_week=6, sequence=1, is_available=False, effective_from=datetime(2026, 1, 1).date(),
        ))
        session.commit()
    report = client.post(f"/organizations/{org['id']}/rosters/{generated['roster_id']}/revalidate")
    assert report.status_code == 200
    kinds = {row["conflict_type"] for row in report.json()["conflicts"]}
    assert "approved_leave" in kinds
    assert "weekly_off" in kinds
    gaps = [row for row in report.json()["conflicts"] if row["conflict_type"] == "process_coverage_gap"]
    assert gaps and gaps[0]["replacement_employee_ids"] == []


def test_revalidation_suggests_only_an_available_eligible_replacement(environment) -> None:
    client, engine = environment
    org, pod, _, _, employees = setup_monthly_scope(client, employee_count=2)
    generated = _generate(client, org, pod).json()
    with Session(engine) as session:
        scheduled = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == generated["roster_id"], RosterEntry.work_date == datetime(2026, 4, 7).date()))
        blocked_employee = scheduled.employee_id
        replacement = next(person["id"] for person in employees if person["id"] != blocked_employee)
        session.add(EmployeeLeave(
            organization_id=org["id"], pod_id=pod["id"], employee_id=blocked_employee,
            starts_at=datetime(2026, 4, 7, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 4, 8, 0, tzinfo=timezone.utc), leave_type="annual", status="approved",
        ))
        session.commit()
    report = client.post(f"/organizations/{org['id']}/rosters/{generated['roster_id']}/revalidate").json()
    coverage_gap = next(row for row in report["conflicts"]
        if row["conflict_type"] == "process_coverage_gap" and row["work_date"] == "2026-04-07")
    assert coverage_gap["replacement_employee_ids"] == [replacement]


def test_validator_reports_brand_level_staffing_gap(environment) -> None:
    client, engine = environment
    org, pod, brand, _, _ = setup_monthly_scope(client)
    generated = _generate(client, org, pod).json()
    with Session(engine) as session:
        session.add(StaffingRequirement(
            organization_id=org["id"], pod_id=pod["id"], brand_id=brand["id"],
            process_id=None, minimum_staff=2, effective_from=datetime(2026, 1, 1).date(),
        ))
        session.commit()
    report = client.post(f"/organizations/{org['id']}/rosters/{generated['roster_id']}/validate").json()
    assert any(row["conflict_type"] == "brand_coverage_gap" for row in report["conflicts"])


def test_validator_detects_duplicate_overlapping_assignments(environment) -> None:
    client, engine = environment
    org, pod, _, process, employees = setup_monthly_scope(client)
    roster_id = _generate(client, org, pod).json()["roster_id"]
    with Session(engine) as session:
        block = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == roster_id, RosterEntry.work_date == datetime(2026, 4, 1).date()))
        duplicate_pa = ProcessAssignment(
            organization_id=org["id"], pod_id=pod["id"], employee_id=employees[0]["id"],
            process_id=process["id"], effective_from=datetime(2026, 1, 2).date(),
        )
        session.add(duplicate_pa)
        session.flush()
        session.add(RosterWorkAssignment(
            organization_id=org["id"], pod_id=pod["id"], roster_entry_id=block.roster_entry_id,
            employee_id=block.employee_id, process_assignment_id=duplicate_pa.id,
            starts_at=block.starts_at, ends_at=block.ends_at,
        ))
        session.commit()
    report = client.post(f"/organizations/{org['id']}/rosters/{roster_id}/validate")
    assert report.status_code == 200
    assert any(row["conflict_type"] == "duplicate_assignment" for row in report.json()["conflicts"])


def test_validator_requires_full_operating_interval_and_merges_adjacent_blocks(environment) -> None:
    client, engine = environment
    org, pod, _, _, _ = setup_monthly_scope(client)
    generated = _generate(client, org, pod).json()
    work_date = datetime(2026, 4, 1).date()
    with Session(engine) as session:
        block = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == generated["roster_id"], RosterEntry.work_date == work_date))
        block.ends_at = datetime(2026, 4, 1, 12, tzinfo=timezone.utc)
        session.commit()

    partial = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/validate"
    ).json()
    gap = next(row for row in partial["conflicts"]
               if row["conflict_type"] == "process_coverage_gap" and row["work_date"] == "2026-04-01")
    assert gap["employee_ids"] == []
    assert gap["sequence"] == 1

    with Session(engine) as session:
        first = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == generated["roster_id"], RosterEntry.work_date == work_date))
        session.add(RosterWorkAssignment(
            organization_id=org["id"], pod_id=pod["id"], roster_entry_id=first.roster_entry_id,
            employee_id=first.employee_id, process_assignment_id=first.process_assignment_id,
            starts_at=datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
            ends_at=datetime(2026, 4, 1, 17, tzinfo=timezone.utc),
        ))
        session.commit()

    complete = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/validate"
    ).json()
    assert complete["valid"] is True
    assert not any(row["conflict_type"] == "process_coverage_gap"
                   and row["work_date"] == "2026-04-01" for row in complete["conflicts"])


def test_validator_keeps_nonoverlapping_assignments_across_processes_valid(environment) -> None:
    client, _ = environment
    org, pod, brand, morning_process, employees = setup_monthly_scope(client)
    process = client.post(f"/organizations/{org['id']}/processes", json={
        "brand_id": brand["id"], "name": "Evening Process", "timezone": "UTC",
    }).json()
    rules = [{
        "day_of_week": weekday, "sequence": 1, "is_open": True,
        "opens_at": "17:00:00", "closes_at": "20:00:00", "effective_from": "2026-01-01",
    } for weekday in range(7)]
    assert client.put(
        f"/organizations/{org['id']}/processes/{process['id']}/calendar",
        json={"timezone": "UTC", "rules": rules},
    ).status_code == 200
    assert client.post(f"/organizations/{org['id']}/staffing-requirements", json={
        "brand_id": brand["id"], "process_id": process["id"],
        "minimum_staff": 1, "effective_from": "2026-01-01",
    }).status_code == 201
    assert client.post(f"/organizations/{org['id']}/employees/{employees[0]['id']}/process-assignments", json={
        "process_id": process["id"], "effective_from": "2026-01-01",
    }).status_code == 201

    generated = _generate(client, org, pod).json()
    assert generated["success"] is True
    assert len(generated["assignments"]) == 60
    assert {row["process_id"] for row in generated["assignments"]} == {
        morning_process["id"], process["id"],
    }
    report = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/validate"
    ).json()
    assert report["valid"] is True


@pytest.mark.parametrize(
    ("second_start", "second_end"),
    [("12:00:00", "16:00:00"), ("15:00:00", "20:00:00")],
    ids=["nested", "partially-overlapping"],
)
def test_validator_does_not_reuse_one_block_for_overlapping_sequences(
    environment, second_start: str, second_end: str,
) -> None:
    client, _ = environment
    org, pod, _, process, _ = setup_monthly_scope(client)
    generated = _generate(client, org, pod).json()
    _add_daily_process_sequence(client, org, process, second_start, second_end)

    report = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/revalidate"
    ).json()

    gap = next(row for row in report["conflicts"]
               if row["conflict_type"] == "process_coverage_gap"
               and row["work_date"] == "2026-04-01" and row["sequence"] == 2)
    assert report["valid"] is False
    assert gap["employee_ids"] == []


def test_overlapping_sequences_validate_when_distinct_employees_cover_each(environment) -> None:
    client, _ = environment
    org, pod, _, process, _ = setup_monthly_scope(client, employee_count=2)
    _add_daily_process_sequence(client, org, process, "12:00:00", "16:00:00")

    response = _generate(client, org, pod)

    assert response.status_code == 201, response.text
    generated = response.json()
    assert generated["success"] is True
    assert len(generated["assignments"]) == 60
    by_date = {}
    for assignment in generated["assignments"]:
        by_date.setdefault(assignment["work_date"], []).append(assignment)
    first_day = by_date["2026-04-01"]
    assert len({assignment["employee_id"] for assignment in first_day}) == 2
    assert {(assignment["starts_at"][11:16], assignment["ends_at"][11:16])
            for assignment in first_day} == {("09:00", "17:00"), ("12:00", "16:00")}


def test_nonoverlapping_sequences_remain_valid_for_one_employee(environment) -> None:
    client, _ = environment
    org, pod, _, process, _ = setup_monthly_scope(client)
    _add_daily_process_sequence(client, org, process, "17:00:00", "20:00:00")

    response = _generate(client, org, pod)

    assert response.status_code == 201, response.text
    generated = response.json()
    assert generated["success"] is True
    assert len(generated["assignments"]) == 60
    assert generated["conflicts"] == []


def test_validator_reports_missing_staffing_but_skips_closed_dates_and_holidays(environment) -> None:
    client, engine = environment
    org, pod, _, process, _ = setup_monthly_scope(client)
    generated = _generate(client, org, pod).json()
    with Session(engine) as session:
        for requirement in session.scalars(select(StaffingRequirement).where(
            StaffingRequirement.organization_id == org["id"])):
            session.delete(requirement)
        session.commit()

    holiday = client.post(f"/organizations/{org['id']}/holidays", json={
        "holiday_date": "2026-04-06", "name": "Closed holiday",
    })
    assert holiday.status_code == 201
    closed = client.post(f"/organizations/{org['id']}/processes/{process['id']}/date-overrides", json={
        "override_date": "2026-04-05", "sequence": 1, "is_open": False,
    })
    assert closed.status_code == 201

    report = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/validate"
    ).json()
    missing = [row for row in report["conflicts"]
               if row["conflict_type"] == "missing_staffing_requirement"]
    assert missing
    assert all(row["process_id"] == process["id"] for row in missing)
    assert all("Monthly Process" in row["message"] for row in missing)
    missing_dates = {row["work_date"] for row in missing}
    assert "2026-04-07" in missing_dates
    assert "2026-04-05" not in missing_dates
    assert "2026-04-06" not in missing_dates


def test_generation_rolls_back_roster_when_independent_validation_fails(environment, monkeypatch) -> None:
    from app.services import roster_validation

    client, engine = environment
    org, pod, _, _, _ = setup_monthly_scope(client)
    conflicts = [{
        "conflict_type": "process_coverage_gap",
        "work_date": datetime(2026, 4, 1).date(),
        "employee_ids": [], "brand_id": 1, "process_id": 1, "sequence": 1,
        "severity": "error", "message": "Injected validation failure for rollback test.",
        "assignment_ids": [], "replacement_employee_ids": [],
        "starts_at": datetime(2026, 4, 1, 9, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 4, 1, 17, tzinfo=timezone.utc),
    }]
    monkeypatch.setattr(roster_validation, "validate_roster", lambda *_: {
        "roster_id": 1, "valid": False, "conflict_count": 1, "conflicts": conflicts,
    })

    response = _generate(client, org, pod)

    assert response.status_code == 409
    result = response.json()
    assert result["success"] is False
    assert result["status"] == "validation_failed"
    assert result["roster_id"] is None
    assert result["conflicts"][0]["message"] == conflicts[0]["message"]
    with Session(engine) as session:
        run = session.get(GenerationRun, result["generation_run_id"])
        assert run.status == "failed"
        assert run.result_summary["solver_status"] == "VALIDATION_FAILED"
        assert run.result_summary["conflicts"][0]["work_date"] == "2026-04-01"
        assert session.scalar(select(Roster.id).where(Roster.pod_id == pod["id"])) is None
        assert session.scalar(select(RosterEntry.id)) is None
        assert session.scalar(select(RosterWorkAssignment.id)) is None


def test_manual_edit_rejects_ineligible_employee_and_accepts_eligible_replacement(environment) -> None:
    client, engine = environment
    org, pod, _, process, employees = setup_monthly_scope(client, employee_count=2)
    generated = _generate(client, org, pod).json()
    with Session(engine) as session:
        block = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == generated["roster_id"]))
    target_pa = client.get(
        f"/organizations/{org['id']}/employees/{employees[1]['id']}/process-assignments"
    ).json()[0]
    base = f"/organizations/{org['id']}/rosters/{generated['roster_id']}/assignments/{block.id}"
    invalid = client.put(base, json={"employee_id": employees[1]["id"], "process_assignment_id": 99999})
    assert invalid.status_code == 409
    valid = client.put(base, json={"employee_id": employees[1]["id"], "process_assignment_id": target_pa["id"]})
    assert valid.status_code == 200
    with Session(engine) as session:
        updated = session.get(RosterWorkAssignment, block.id)
        assert updated.employee_id == employees[1]["id"]


def test_assignment_candidates_endpoint_applies_availability_and_pod_eligibility(environment) -> None:
    client, engine = environment
    org, pod, _, process, employees = setup_monthly_scope(client, employee_count=2)
    generated = _generate(client, org, pod).json()
    with Session(engine) as session:
        block = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == generated["roster_id"],
            RosterEntry.work_date == datetime(2026, 4, 1).date(),
        ))
        assert block is not None
        assignment_id = block.id
        current_employee_id = block.employee_id
        replacement = next(row for row in employees if row["id"] != current_employee_id)

    endpoint = (
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}"
        f"/assignments/{assignment_id}/candidates"
    )
    candidates = client.get(endpoint)
    assert candidates.status_code == 200
    assert candidates.json() == {
        "roster_id": generated["roster_id"],
        "assignment_id": assignment_id,
        "candidate_employee_ids": [replacement["id"]],
    }

    unavailable = client.post(
        f"/organizations/{org['id']}/employees/{replacement['id']}/availability",
        json={"day_of_week": 2, "sequence": 1, "is_available": False,
              "effective_from": "2026-01-01"},
    )
    assert unavailable.status_code == 201
    filtered = client.get(endpoint)
    assert filtered.status_code == 200
    assert filtered.json()["candidate_employee_ids"] == []
    assert client.get(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}"
        "/assignments/999999/candidates"
    ).status_code == 404


def test_locked_assignment_survives_regeneration_as_locked(environment) -> None:
    client, engine = environment
    org, pod, _, _, employees = setup_monthly_scope(client, employee_count=2)
    first = _generate(client, org, pod).json()
    with Session(engine) as session:
        block = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == first["roster_id"]))
        block_id = block.id
        employee_id = block.employee_id
        work_date = session.get(RosterEntry, block.roster_entry_id).work_date
    locked = client.patch(
        f"/organizations/{org['id']}/rosters/{first['roster_id']}/assignments/{block_id}/lock",
        json={"is_locked": True},
    )
    assert locked.status_code == 200 and locked.json()["is_locked"] is True
    regenerated = _generate(client, org, pod, preserve=first["roster_id"])
    assert regenerated.status_code == 201
    result = regenerated.json()
    with Session(engine) as session:
        preserved = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == result["roster_id"], RosterWorkAssignment.employee_id == employee_id,
            RosterEntry.work_date == work_date, RosterWorkAssignment.is_locked.is_(True)))
        assert preserved is not None


def test_unavailable_manual_lock_blocks_regeneration_and_reports_unresolved_gap(environment) -> None:
    client, engine = environment
    org, pod, _, _, employees = setup_monthly_scope(client, employee_count=2)
    first = _generate(client, org, pod).json()
    with Session(engine) as session:
        block = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == first["roster_id"]))
        employee_id = block.employee_id
        assignment_id = block.id
        day = session.get(RosterEntry, block.roster_entry_id).work_date
    locked = client.patch(
        f"/organizations/{org['id']}/rosters/{first['roster_id']}/assignments/{assignment_id}/lock",
        json={"is_locked": True},
    )
    assert locked.status_code == 200
    unavailable = client.post(
        f"/organizations/{org['id']}/employees/{employee_id}/availability",
        json={"day_of_week": day.weekday(), "sequence": 1, "is_available": False, "effective_from": "2026-01-01"},
    )
    assert unavailable.status_code == 201
    revalidation = client.post(f"/organizations/{org['id']}/rosters/{first['roster_id']}/revalidate").json()
    assert any(row["conflict_type"] == "weekly_off" for row in revalidation["conflicts"])
    regenerated = _generate(client, org, pod, preserve=first["roster_id"])
    assert regenerated.status_code == 409
    assert regenerated.json()["unresolved_conflicts"]
    assert any(row["work_date"] == day.isoformat() for row in regenerated.json()["unresolved_conflicts"])


def test_calendar_availability_conflict_repair_and_edit_workflow(environment) -> None:
    client, engine = environment
    org, pod, _, process, employees = setup_monthly_scope(client, employee_count=3)
    calendar_url = f"/organizations/{org['id']}/processes/{process['id']}/calendar"
    holiday = client.post(
        f"/organizations/{org['id']}/holidays",
        json={"holiday_date": "2026-04-05", "name": "Demo Holiday"},
    )
    assert holiday.status_code == 201
    weekly_off = client.post(
        f"/organizations/{org['id']}/employees/{employees[0]['id']}/availability",
        json={"day_of_week": 6, "sequence": 1, "is_available": False,
              "effective_from": "2026-01-01"},
    )
    assert weekly_off.status_code == 201

    generated_response = _generate(client, org, pod)
    assert generated_response.status_code == 201
    generated = generated_response.json()
    assert generated["success"] is True
    assert all(row["uncovered"] == 0 for row in generated["coverage"])
    assert not any(row["work_date"] == "2026-04-05" for row in generated["assignments"])
    assert not any(
        row["employee_id"] == employees[0]["id"]
        and datetime.fromisoformat(row["work_date"]).weekday() == 6
        for row in generated["assignments"]
    )
    assert client.get(
        f"{calendar_url}/effective?start_date=2026-04-05&end_date=2026-04-05"
    ).json()[0]["is_open"] is False

    with Session(engine) as session:
        block = session.scalar(select(RosterWorkAssignment).join(RosterEntry).where(
            RosterEntry.roster_id == generated["roster_id"],
            RosterEntry.work_date == datetime(2026, 4, 7).date(),
        ))
        assert block is not None
        assigned_employee_id = block.employee_id
        assignment_id = block.id
        remaining_ids = {row["id"] for row in employees} - {assigned_employee_id}
        replacement_id = min(remaining_ids)

    # Leave creation is covered through its API in the assessment integration tests.
    # Insert this approved leave directly to focus this test on saved-roster revalidation and editing.
    with Session(engine) as session:
        session.add(EmployeeLeave(
            organization_id=org["id"], pod_id=pod["id"], employee_id=assigned_employee_id,
            starts_at=datetime(2026, 4, 7, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 4, 8, 0, tzinfo=timezone.utc),
            leave_type="annual", status="approved",
        ))
        session.commit()

    report = client.post(f"/organizations/{org['id']}/rosters/{generated['roster_id']}/revalidate")
    assert report.status_code == 200
    conflicts = report.json()["conflicts"]
    assert any(row["conflict_type"] == "approved_leave" for row in conflicts)
    gap = next(
        row for row in conflicts
        if row["conflict_type"] == "process_coverage_gap" and row["work_date"] == "2026-04-07"
    )
    assert replacement_id in gap["replacement_employee_ids"]

    replacement_assignment = client.get(
        f"/organizations/{org['id']}/employees/{replacement_id}/process-assignments"
    ).json()[0]
    edited = client.put(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/assignments/{assignment_id}",
        json={"employee_id": replacement_id, "process_assignment_id": replacement_assignment["id"]},
    )
    assert edited.status_code == 200
    assert edited.json()["employee_id"] == replacement_id
    final_report = client.post(
        f"/organizations/{org['id']}/rosters/{generated['roster_id']}/validate"
    )
    assert final_report.status_code == 200
    assert final_report.json()["valid"] is True


def test_december_2026_generation_uses_recurring_calendar_effective_from_january(environment) -> None:
    client, _ = environment
    org, pod, _, _, _ = setup_monthly_scope(client, employee_count=1)

    response = client.post(
        f"/organizations/{org['id']}/rosters/generate",
        json={"pod_id": pod["id"], "year": 2026, "month": 12,
              "solver_time_limit_seconds": 5},
    )

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["success"] is True
    assert result["status"] == "succeeded"
    assert len(result["assignments"]) == 31
    assert len(result["coverage"]) == 31
    assert all(row["uncovered"] == 0 for row in result["coverage"])


def test_december_monday_availability_creates_weekly_off_and_coverage_conflicts(environment) -> None:
    client, _ = environment
    org, pod, _, _, employees = setup_monthly_scope(client, employee_count=1)
    generated = client.post(
        f"/organizations/{org['id']}/rosters/generate",
        json={"pod_id": pod["id"], "year": 2026, "month": 12,
              "solver_time_limit_seconds": 5},
    )
    assert generated.status_code == 201
    roster = generated.json()

    saved_rule = client.post(
        f"/organizations/{org['id']}/employees/{employees[0]['id']}/availability",
        json={"day_of_week": 0, "sequence": 1, "is_available": False,
              "effective_from": "2026-12-01", "effective_to": "2026-12-31"},
    )
    assert saved_rule.status_code == 201
    assert saved_rule.json()["effective_from"] == "2026-12-01"
    assert saved_rule.json()["effective_to"] == "2026-12-31"

    report = client.post(f"/organizations/{org['id']}/rosters/{roster['roster_id']}/revalidate")
    assert report.status_code == 200
    assert report.json()["valid"] is False
    weekly_offs = [row for row in report.json()["conflicts"] if row["conflict_type"] == "weekly_off"]
    coverage_gaps = [row for row in report.json()["conflicts"]
                     if row["conflict_type"] == "process_coverage_gap"]
    expected_mondays = {"2026-12-07", "2026-12-14", "2026-12-21", "2026-12-28"}
    assert {row["work_date"] for row in weekly_offs} == expected_mondays
    assert {row["work_date"] for row in coverage_gaps} == expected_mondays
