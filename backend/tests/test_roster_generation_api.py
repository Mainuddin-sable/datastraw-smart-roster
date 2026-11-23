from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.db.base import Base
from app.main import app
from app.models import EmployeeAvailability, EmployeeLeave, GenerationRun, ProcessAssignment, Roster, RosterEntry, RosterWorkAssignment, StaffingRequirement


@pytest.fixture
def environment() -> Iterator[tuple[TestClient, object]]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
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
