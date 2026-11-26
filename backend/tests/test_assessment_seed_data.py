from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.db.base import Base
from app.main import app
from app.models import (
    Employee, Organization, PodLead, Process, ProcessAssignment, RosterEntry, RosterWorkAssignment,
    StaffingRequirement,
)
from scripts import seed_assessment_demo
from auth_support import TEST_AUTH_HEADERS


def test_assessment_seed_refuses_a_populated_database_without_writing(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_assessment_demo, "SessionLocal", session_factory)

    with Session(engine) as session:
        session.add(Organization(name="Existing organization"))
        session.commit()

    def unexpected_api_client():
        raise AssertionError("The assessment seed must stop before making API calls.")

    monkeypatch.setattr(seed_assessment_demo, "authenticated_test_client", unexpected_api_client)
    try:
        with pytest.raises(RuntimeError, match="Refusing to seed a non-empty database"):
            seed_assessment_demo.main()
        with Session(engine) as session:
            assert session.scalar(select(func.count(Organization.id))) == 1
            assert session.scalar(select(func.count(Employee.id))) == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_assessment_seed_has_required_distribution_and_each_pod_generates(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_assessment_demo, "SessionLocal", session_factory)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        seed_assessment_demo.main()
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            organization = client.get("/organizations").json()[0]
            org_id = organization["id"]
            pods = client.get(f"/organizations/{org_id}/pods").json()
            brands = client.get(f"/organizations/{org_id}/brands").json()
            processes = client.get(f"/organizations/{org_id}/processes").json()
            employees = client.get(f"/organizations/{org_id}/employees").json()

            assert (len(pods), len(brands), len(processes), len(employees)) == (3, 23, 28, 30)
            assert all(pod["lead_employee_id"] is None for pod in pods)
            with Session(engine) as session:
                rows = session.execute(
                    select(Process.brand_id, ProcessAssignment.employee_id)
                    .join(ProcessAssignment, ProcessAssignment.process_id == Process.id)
                ).all()
            employee_ids_by_brand: dict[int, set[int]] = {}
            brand_ids_by_employee: dict[int, set[int]] = {}
            for brand_id, employee_id in rows:
                employee_ids_by_brand.setdefault(brand_id, set()).add(employee_id)
                brand_ids_by_employee.setdefault(employee_id, set()).add(brand_id)
            assert set(brand_ids_by_employee) == {employee["id"] for employee in employees}
            counts = {brand["id"]: len(employee_ids_by_brand.get(brand["id"], set())) for brand in brands}
            assert sum(size == 1 for size in counts.values()) == 18
            assert sum(size in (2, 3) for size in counts.values()) == 5
            assert all(size in (1, 2, 3) for size in counts.values())
            assert sum(len(brand_ids) > 1 for brand_ids in brand_ids_by_employee.values()) == 2
            sana = next(employee for employee in employees if employee["full_name"] == "Sana Khan")
            assessment_brand_07 = next(brand for brand in brands if brand["name"] == "Assessment Brand 07")
            brand_07_core = next(process for process in processes
                                 if process["brand_id"] == assessment_brand_07["id"]
                                 and process["name"] == "Core Operations")
            sana_assignments = client.get(
                f"/organizations/{org_id}/employees/{sana['id']}/process-assignments"
            ).json()
            assert [row["process_id"] for row in sana_assignments] == [brand_07_core["id"]]
            with Session(engine) as session:
                assert (session.scalar(select(func.max(StaffingRequirement.minimum_staff))) or 0) == 2

            holidays = client.get(
                f"/organizations/{org_id}/holidays",
                params={"start_date": "2026-12-25", "end_date": "2026-12-25"},
            ).json()
            assert len(holidays) == 1

            for pod in pods:
                response = client.post(
                    f"/organizations/{org_id}/rosters/generate",
                    json={"pod_id": pod["id"], "year": 2026, "month": 12,
                          "solver_time_limit_seconds": 15},
                )
                assert response.status_code == 201, response.text
                roster = response.json()
                assert roster["success"] is True
                assert roster["assignments"]
                validation = client.post(
                    f"/organizations/{org_id}/rosters/{roster['roster_id']}/validate",
                )
                assert validation.status_code == 200
                assert validation.json()["valid"] is True

            single_brand = next(brand for brand in brands if counts[brand["id"]] == 1)
            single_process = next(process for process in processes if process["brand_id"] == single_brand["id"])
            weekly_rows = client.get(
                f"/organizations/{org_id}/processes/{single_process['id']}/calendar/effective",
                params={"start_date": "2026-12-06", "end_date": "2026-12-06"},
            ).json()
            assert weekly_rows[0]["is_open"] is False

            shared_process_ids = [process["id"] for process in processes if process["name"] == "Core Operations"]
            holiday_open = False
            for process_id in shared_process_ids:
                days = client.get(
                    f"/organizations/{org_id}/processes/{process_id}/calendar/effective",
                    params={"start_date": "2026-12-25", "end_date": "2026-12-25"},
                ).json()
                holiday_open = holiday_open or days[0]["is_open"]
            assert holiday_open is True

        with Session(engine) as session:
            assert session.scalar(select(func.count(PodLead.pod_id))) == 0
            assert session.scalar(select(func.count(ProcessAssignment.id))) > 30
            assert session.scalar(select(func.count(Employee.id))) == 30
            assert session.scalar(select(func.count(Process.id))) == 28
            assert session.scalar(select(func.count(Organization.id))) == 1
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_sole_owner_approved_leave_exposes_gap_without_inventing_replacement(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_assessment_demo, "SessionLocal", session_factory)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            organization = client.post("/organizations", json={"name": "Assessment Leave Demo"})
            assert organization.status_code == 201, organization.text
            organization_id = organization.json()["id"]
            seeded = seed_assessment_demo.seed_assessment_demo(client, organization_id)

            brand_id, kind, team_ids = next(row for row in seeded["brands"] if row[1] == "solo")
            assert kind == "solo"
            owner_id = team_ids[0]
            brands = client.get(f"/organizations/{organization_id}/brands").json()
            brand = next(row for row in brands if row["id"] == brand_id)
            process = next(row for row in client.get(
                f"/organizations/{organization_id}/processes"
            ).json() if row["brand_id"] == brand_id)
            pod_id = brand["pod_id"]

            generated = client.post(
                f"/organizations/{organization_id}/rosters/generate",
                json={"pod_id": pod_id, "year": 2026, "month": 12,
                      "solver_time_limit_seconds": 15},
            )
            assert generated.status_code == 201, generated.text
            roster = generated.json()
            assert roster["success"] is True
            target = next(row for row in roster["assignments"]
                          if row["process_id"] == process["id"]
                          and row["work_date"] == "2026-12-01")
            assert target["employee_id"] == owner_id

            unrelated = next(row for row in roster["assignments"]
                             if row["process_id"] != process["id"])
            locked = client.patch(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                f"/assignments/{unrelated['assignment_id']}/lock",
                json={"is_locked": True},
            )
            assert locked.status_code == 200, locked.text
            assert locked.json()["is_locked"] is True

            leave = client.post(
                f"/organizations/{organization_id}/leave-requests",
                json={"employee_id": owner_id,
                      "starts_at": "2026-12-01T09:00:00+05:30",
                      "ends_at": "2026-12-01T17:00:00+05:30",
                      "leave_type": "annual", "reason": "Assessment scenario"},
            )
            assert leave.status_code == 201, leave.text
            leave_id = leave.json()["id"]
            assert leave.json()["status"] == "requested"
            approval = client.patch(
                f"/organizations/{organization_id}/leave-requests/{leave_id}",
                json={"status": "approved"},
            )
            assert approval.status_code == 200, approval.text
            assert approval.json()["status"] == "approved"

            validation = client.post(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}/revalidate"
            )
            assert validation.status_code == 200, validation.text
            conflicts = validation.json()["conflicts"]
            assert any(row["conflict_type"] == "approved_leave"
                       and row["process_id"] == process["id"]
                       and row["work_date"] == "2026-12-01" for row in conflicts)
            gap = next(row for row in conflicts
                       if row["conflict_type"] == "process_coverage_gap"
                       and row["process_id"] == process["id"]
                       and row["work_date"] == "2026-12-01")
            assert gap["replacement_employee_ids"] == []

            candidate_response = client.get(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                f"/assignments/{target['assignment_id']}/candidates"
            )
            assert candidate_response.status_code == 200, candidate_response.text
            assert candidate_response.json()["candidate_employee_ids"] == []

            # The seed gives this process exactly one eligible employee, who is
            # now on approved leave. No replacement can be applied without
            # changing the sample's eligibility data or weakening the rules.
            with Session(engine) as session:
                eligible_ids = set(session.scalars(select(ProcessAssignment.employee_id).where(
                    ProcessAssignment.process_id == process["id"],
                    ProcessAssignment.effective_from <= date(2026, 12, 1),
                    (ProcessAssignment.effective_to.is_(None)
                     | (ProcessAssignment.effective_to >= date(2026, 12, 1))),
                )))
                assert eligible_ids == {owner_id}
                stored = {
                    row.id: (row.employee_id, row.is_locked)
                    for row in session.scalars(select(RosterWorkAssignment)
                        .join(RosterEntry, RosterEntry.id == RosterWorkAssignment.roster_entry_id).where(
                        RosterWorkAssignment.organization_id == organization_id,
                        RosterWorkAssignment.pod_id == pod_id,
                        RosterEntry.roster_id == roster["roster_id"],
                    ))
                }
                assert stored[target["assignment_id"]] == (owner_id, False)
                assert stored[unrelated["assignment_id"]] == (unrelated["employee_id"], True)
                assert {assignment_id: values[0] for assignment_id, values in stored.items()} == {
                    row["assignment_id"]: row["employee_id"] for row in roster["assignments"]
                }
                assert {assignment_id for assignment_id, values in stored.items() if values[1]} == {
                    unrelated["assignment_id"]
                }
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_shared_brand_specialist_leave_uses_only_a_free_eligible_backup(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_assessment_demo, "SessionLocal", session_factory)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            organization = client.post("/organizations", json={"name": "Assessment Shared Leave Demo"})
            assert organization.status_code == 201, organization.text
            organization_id = organization.json()["id"]
            seed_assessment_demo.seed_assessment_demo(client, organization_id)

            brands = client.get(f"/organizations/{organization_id}/brands").json()
            brand = next(row for row in brands if row["name"] == "Assessment Brand 08")
            brand_id, pod_id = brand["id"], brand["pod_id"]
            brand_07 = next(row for row in brands if row["name"] == "Assessment Brand 07")
            all_processes = client.get(f"/organizations/{organization_id}/processes").json()
            brand_07_core = next(row for row in all_processes
                                 if row["brand_id"] == brand_07["id"]
                                 and row["name"] == "Core Operations")
            pods = client.get(f"/organizations/{organization_id}/pods").json()
            assert next(row["name"] for row in pods if row["id"] == pod_id) == "Customer Care"
            employees = client.get(f"/organizations/{organization_id}/employees").json()
            employee_id_by_name = {row["full_name"]: row["id"] for row in employees}
            kabir_id = employee_id_by_name["Kabir Shah"]
            neha_id = employee_id_by_name["Neha Verma"]
            rohan_id = employee_id_by_name["Rohan Das"]
            sana_id = employee_id_by_name["Sana Khan"]

            processes = client.get(f"/organizations/{organization_id}/processes").json()
            core = next(row for row in processes
                        if row["brand_id"] == brand_id and row["name"] == "Core Operations")
            specialist = next(row for row in processes
                              if row["brand_id"] == brand_id and row["name"] == "Specialist Operations")
            with Session(engine) as session:
                requirement_by_process = {
                    row.process_id: row.minimum_staff
                    for row in session.scalars(select(StaffingRequirement).where(
                        StaffingRequirement.brand_id == brand_id,
                    ))
                }
                process_employees = {
                    process_id: set(session.scalars(select(ProcessAssignment.employee_id).where(
                        ProcessAssignment.process_id == process_id,
                        ProcessAssignment.effective_from <= date(2026, 12, 1),
                        (ProcessAssignment.effective_to.is_(None)
                         | (ProcessAssignment.effective_to >= date(2026, 12, 1))),
                    )))
                    for process_id in (core["id"], specialist["id"])
                }
            assert requirement_by_process == {core["id"]: 2, specialist["id"]: 1}
            assert process_employees[core["id"]] == {kabir_id, neha_id}
            assert process_employees[specialist["id"]] == {kabir_id, rohan_id}
            assert sana_id not in process_employees[core["id"]] | process_employees[specialist["id"]]
            sana_assignments = client.get(
                f"/organizations/{organization_id}/employees/{sana_id}/process-assignments"
            ).json()
            assert [row["process_id"] for row in sana_assignments] == [brand_07_core["id"]]
            assert client.get(
                f"/organizations/{organization_id}/employees/{kabir_id}/availability",
                params={"start_date": "2026-12-01", "end_date": "2026-12-01"},
            ).json() == []

            generated = client.post(
                f"/organizations/{organization_id}/rosters/generate",
                json={"pod_id": pod_id, "year": 2026, "month": 12,
                      "solver_time_limit_seconds": 15},
            )
            assert generated.status_code == 201, generated.text
            roster = generated.json()
            assert roster["success"] is True
            assignments = roster["assignments"]
            rohan_specialist_rows = [row for row in assignments
                                     if row["process_id"] == specialist["id"]
                                     and row["employee_id"] == rohan_id]
            assert rohan_specialist_rows, "Rohan was not assigned to Brand 08 Specialist Operations."
            collisions = []
            chosen = None
            for target in rohan_specialist_rows:
                candidates_response = client.get(
                    f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                    f"/assignments/{target['assignment_id']}/candidates"
                )
                assert candidates_response.status_code == 200, candidates_response.text
                candidates = candidates_response.json()["candidate_employee_ids"]
                assert set(candidates) <= {kabir_id}
                if kabir_id in candidates:
                    chosen = target
                    break
                target_start = datetime.fromisoformat(target["starts_at"])
                target_end = datetime.fromisoformat(target["ends_at"])
                conflicting_blocks = [row for row in assignments
                    if row["employee_id"] == kabir_id
                    and row["work_date"] == target["work_date"]
                    and datetime.fromisoformat(row["starts_at"]) < target_end
                    and datetime.fromisoformat(row["ends_at"]) > target_start]
                assert conflicting_blocks, "Kabir was excluded without an overlapping saved assignment."
                assert all(row["brand_id"] != brand_id for row in conflicting_blocks)
                assert all(row["brand_name"] == "Assessment Brand 07" for row in conflicting_blocks)
                collisions.append((target["work_date"], [
                    (row["brand_name"], row["process_name"]) for row in conflicting_blocks
                ]))

            has_free_backup = chosen is not None
            target = chosen if chosen is not None else rohan_specialist_rows[0]

            date_assignments = [row for row in assignments if row["work_date"] == target["work_date"]]
            unrelated = next(row for row in date_assignments
                             if row["process_id"] not in {core["id"], specialist["id"]}
                             and row["employee_id"] not in {kabir_id, rohan_id})
            lock_response = client.patch(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                f"/assignments/{unrelated['assignment_id']}/lock",
                json={"is_locked": True},
            )
            assert lock_response.status_code == 200, lock_response.text
            assert lock_response.json()["is_locked"] is True

            leave = client.post(
                f"/organizations/{organization_id}/leave-requests",
                json={"employee_id": rohan_id, "starts_at": target["starts_at"],
                      "ends_at": target["ends_at"], "leave_type": "annual",
                      "reason": "Assessment shared-brand replacement scenario"},
            )
            assert leave.status_code == 201, leave.text
            assert leave.json()["status"] == "requested"
            approved = client.patch(
                f"/organizations/{organization_id}/leave-requests/{leave.json()['id']}",
                json={"status": "approved"},
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "approved"

            before = client.post(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}/revalidate"
            )
            assert before.status_code == 200, before.text
            assert before.json()["valid"] is False
            assert any(row["conflict_type"] == "approved_leave"
                       and row["employee_ids"] == [rohan_id]
                       for row in before.json()["conflicts"])
            gap = next(row for row in before.json()["conflicts"]
                       if row["conflict_type"] == "process_coverage_gap"
                       and row["process_id"] == specialist["id"]
                       and row["work_date"] == target["work_date"])
            expected_candidates = [kabir_id] if has_free_backup else []
            assert gap["replacement_employee_ids"] == expected_candidates

            saved_candidates = client.get(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                f"/assignments/{target['assignment_id']}/candidates"
            )
            assert saved_candidates.status_code == 200, saved_candidates.text
            assert saved_candidates.json()["candidate_employee_ids"] == expected_candidates
            if has_free_backup:
                kabir_process_assignment = next(row for row in client.get(
                    f"/organizations/{organization_id}/employees/{kabir_id}/process-assignments"
                ).json() if row["process_id"] == specialist["id"])
                edited = client.put(
                    f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                    f"/assignments/{target['assignment_id']}",
                    json={"employee_id": kabir_id,
                          "process_assignment_id": kabir_process_assignment["id"]},
                )
                assert edited.status_code == 200, edited.text
                assert edited.json()["employee_id"] == kabir_id

            after = client.post(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}/revalidate"
            )
            assert after.status_code == 200, after.text
            if has_free_backup:
                assert after.json()["valid"] is True, after.json()["conflicts"]
                assert after.json()["conflicts"] == []
            else:
                assert after.json()["valid"] is False
                assert {row["conflict_type"] for row in after.json()["conflicts"]} == {
                    "approved_leave", "process_coverage_gap",
                }
                assert all(row["process_id"] == specialist["id"]
                           for row in after.json()["conflicts"])
                assert collisions
                assert process_employees[specialist["id"]] - {rohan_id} == {kabir_id}

            with Session(engine) as session:
                stored = {
                    row.id: (row.employee_id, row.is_locked)
                    for row in session.scalars(select(RosterWorkAssignment)
                        .join(RosterEntry, RosterEntry.id == RosterWorkAssignment.roster_entry_id).where(
                            RosterEntry.roster_id == roster["roster_id"],
                        ))
                }
            expected_employees = {row["assignment_id"]: row["employee_id"] for row in assignments}
            if has_free_backup:
                expected_employees[target["assignment_id"]] = kabir_id
            assert {assignment_id: values[0] for assignment_id, values in stored.items()} == expected_employees
            assert stored[unrelated["assignment_id"]] == (unrelated["employee_id"], True)
            assert {assignment_id for assignment_id, values in stored.items() if values[1]} == {
                unrelated["assignment_id"]
            }
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_assessment_back_office_core_leave_is_replaced_by_free_shared_brand_employee(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_assessment_demo, "SessionLocal", session_factory)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            organization = client.post("/organizations", json={"name": "Assessment Replacement Demo"})
            assert organization.status_code == 201, organization.text
            organization_id = organization.json()["id"]
            seed_assessment_demo.seed_assessment_demo(client, organization_id)

            pods = client.get(f"/organizations/{organization_id}/pods").json()
            assert len(pods) == 3
            back_office = next(row for row in pods if row["name"] == "Back Office")
            brands = client.get(f"/organizations/{organization_id}/brands").json()
            brand = next(row for row in brands if row["name"] == "Assessment Brand 23")
            assert brand["pod_id"] == back_office["id"]
            brand_id = brand["id"]

            employees = client.get(f"/organizations/{organization_id}/employees").json()
            employee_id_by_name = {row["full_name"]: row["id"] for row in employees}
            chirag_id = employee_id_by_name["Chirag Shetty"]
            esha_id = employee_id_by_name["Esha Dutta"]
            processes = client.get(f"/organizations/{organization_id}/processes").json()
            core = next(row for row in processes
                        if row["brand_id"] == brand_id and row["name"] == "Core Operations")
            specialist = next(row for row in processes
                              if row["brand_id"] == brand_id and row["name"] == "Specialist Operations")
            with Session(engine) as session:
                requirements = {
                    row.process_id: row.minimum_staff
                    for row in session.scalars(select(StaffingRequirement).where(
                        StaffingRequirement.brand_id == brand_id,
                    ))
                }
                eligible = {
                    process_id: set(session.scalars(select(ProcessAssignment.employee_id).where(
                        ProcessAssignment.process_id == process_id,
                        ProcessAssignment.effective_from <= date(2026, 12, 1),
                        (ProcessAssignment.effective_to.is_(None)
                         | (ProcessAssignment.effective_to >= date(2026, 12, 1))),
                    )))
                    for process_id in (core["id"], specialist["id"])
                }
            assert requirements == {core["id"]: 1, specialist["id"]: 1}
            assert eligible[core["id"]] == {chirag_id, esha_id}
            assert eligible[specialist["id"]] == {chirag_id}
            for employee_id in (chirag_id, esha_id):
                assert client.get(
                    f"/organizations/{organization_id}/employees/{employee_id}/availability",
                    params={"start_date": "2026-12-01", "end_date": "2026-12-31"},
                ).json() == []

            rosters_by_pod = {}
            for pod in pods:
                generated = client.post(
                    f"/organizations/{organization_id}/rosters/generate",
                    json={"pod_id": pod["id"], "year": 2026, "month": 12,
                          "solver_time_limit_seconds": 15},
                )
                assert generated.status_code == 201, generated.text
                roster = generated.json()
                assert roster["success"] is True
                validation = client.post(
                    f"/organizations/{organization_id}/rosters/{roster['roster_id']}/validate"
                )
                assert validation.status_code == 200, validation.text
                assert validation.json()["valid"] is True, validation.json()["conflicts"]
                rosters_by_pod[pod["id"]] = roster

            roster = rosters_by_pod[back_office["id"]]
            assignments = roster["assignments"]
            core_blocks = [row for row in assignments if row["process_id"] == core["id"]]
            assert core_blocks
            chosen = None
            replacement_id = None
            for target in core_blocks:
                candidates = client.get(
                    f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                    f"/assignments/{target['assignment_id']}/candidates"
                )
                assert candidates.status_code == 200, candidates.text
                candidate_ids = candidates.json()["candidate_employee_ids"]
                expected_other = esha_id if target["employee_id"] == chirag_id else chirag_id
                assert set(candidate_ids) <= {expected_other}
                if expected_other in candidate_ids:
                    chosen = target
                    replacement_id = expected_other
                    break
            assert chosen is not None, "No genuinely free same-brand employee was returned by candidate lookup."

            assignments_on_date = [row for row in assignments if row["work_date"] == chosen["work_date"]]
            unrelated = next(row for row in assignments_on_date
                             if row["process_id"] not in {core["id"], specialist["id"]}
                             and row["employee_id"] not in {chosen["employee_id"], replacement_id})
            locked = client.patch(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                f"/assignments/{unrelated['assignment_id']}/lock",
                json={"is_locked": True},
            )
            assert locked.status_code == 200, locked.text
            assert locked.json()["is_locked"] is True

            leave = client.post(
                f"/organizations/{organization_id}/leave-requests",
                json={"employee_id": chosen["employee_id"], "starts_at": chosen["starts_at"],
                      "ends_at": chosen["ends_at"], "leave_type": "annual",
                      "reason": "Assessment shared-brand replacement scenario"},
            )
            assert leave.status_code == 201, leave.text
            assert leave.json()["status"] == "requested"
            approved = client.patch(
                f"/organizations/{organization_id}/leave-requests/{leave.json()['id']}",
                json={"status": "approved"},
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "approved"

            before = client.post(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}/revalidate"
            )
            assert before.status_code == 200, before.text
            assert before.json()["valid"] is False
            assert any(row["conflict_type"] == "approved_leave"
                       and row["employee_ids"] == [chosen["employee_id"]]
                       for row in before.json()["conflicts"])
            gap = next(row for row in before.json()["conflicts"]
                       if row["conflict_type"] == "process_coverage_gap"
                       and row["process_id"] == core["id"]
                       and row["work_date"] == chosen["work_date"])
            assert gap["replacement_employee_ids"] == [replacement_id]

            candidates = client.get(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                f"/assignments/{chosen['assignment_id']}/candidates"
            )
            assert candidates.status_code == 200, candidates.text
            assert candidates.json()["candidate_employee_ids"] == [replacement_id]
            process_assignment = next(row for row in client.get(
                f"/organizations/{organization_id}/employees/{replacement_id}/process-assignments"
            ).json() if row["process_id"] == core["id"])
            replacement = client.put(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}"
                f"/assignments/{chosen['assignment_id']}",
                json={"employee_id": replacement_id,
                      "process_assignment_id": process_assignment["id"]},
            )
            assert replacement.status_code == 200, replacement.text
            assert replacement.json()["employee_id"] == replacement_id

            after = client.post(
                f"/organizations/{organization_id}/rosters/{roster['roster_id']}/revalidate"
            )
            assert after.status_code == 200, after.text
            assert after.json()["valid"] is True, after.json()["conflicts"]
            assert after.json()["conflicts"] == []
            assert not any(row["conflict_type"] == "overlapping_assignments"
                           for row in after.json()["conflicts"])

            with Session(engine) as session:
                stored = {
                    row.id: (row.employee_id, row.is_locked)
                    for row in session.scalars(select(RosterWorkAssignment)
                        .join(RosterEntry, RosterEntry.id == RosterWorkAssignment.roster_entry_id).where(
                            RosterEntry.roster_id == roster["roster_id"],
                        ))
                }
            expected_employees = {row["assignment_id"]: row["employee_id"] for row in assignments}
            expected_employees[chosen["assignment_id"]] = replacement_id
            assert {assignment_id: values[0] for assignment_id, values in stored.items()} == expected_employees
            assert stored[unrelated["assignment_id"]] == (unrelated["employee_id"], True)
            assert {assignment_id for assignment_id, values in stored.items() if values[1]} == {
                unrelated["assignment_id"]
            }
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
