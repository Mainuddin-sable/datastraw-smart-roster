from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.db.base import Base
from app.main import app
from scripts import seed_demo_data
from scripts.seed_demo_data import ensure_original_pod_demo_demand, ensure_process_eligibility
from auth_support import TEST_AUTH_HEADERS


def test_demo_seed_fills_only_missing_effective_eligibility_dates() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            organization = client.post("/organizations", json={"name": "Seed regression"}).json()
            pod = client.post(
                f"/organizations/{organization['id']}/pods", json={"name": "Seed Pod"}
            ).json()
            brand = client.post(
                f"/organizations/{organization['id']}/brands",
                json={"pod_id": pod["id"], "name": "Seed Brand"},
            ).json()
            process = client.post(
                f"/organizations/{organization['id']}/processes",
                json={"brand_id": brand["id"], "name": "Seed Process"},
            ).json()
            employee_ids = []
            for name, email in (("Prior Period", "prior@example.test"),
                                 ("Future Period", "future@example.test")):
                employee = client.post(
                    f"/organizations/{organization['id']}/employees",
                    json={"pod_id": pod["id"], "full_name": name, "email": email},
                ).json()
                employee_ids.append(employee["id"])

            prior = client.post(
                f"/organizations/{organization['id']}/employees/{employee_ids[0]}/process-assignments",
                json={"process_id": process["id"], "effective_from": "2026-01-01",
                      "effective_to": "2026-04-30"},
            )
            future = client.post(
                f"/organizations/{organization['id']}/employees/{employee_ids[1]}/process-assignments",
                json={"process_id": process["id"], "effective_from": "2027-01-01"},
            )
            assert prior.status_code == future.status_code == 201

            first_rows = ensure_process_eligibility(
                client, organization["id"], employee_ids[0], process["id"],
                "2026-01-01", "2026-12-31",
            )
            second_rows = ensure_process_eligibility(
                client, organization["id"], employee_ids[1], process["id"],
                "2026-01-01", "2026-12-31",
            )
            assert sorted((row["effective_from"], row["effective_to"]) for row in first_rows) == [
                ("2026-01-01", "2026-04-30"), ("2026-05-01", "2026-12-31"),
            ]
            assert sorted((row["effective_from"], row["effective_to"]) for row in second_rows) == [
                ("2026-01-01", "2026-12-31"), ("2027-01-01", None),
            ]

            for employee_id in employee_ids:
                repeated = ensure_process_eligibility(
                    client, organization["id"], employee_id, process["id"],
                    "2026-01-01", "2026-12-31",
                )
                assert len(repeated) == 2
                assert client.get(
                    f"/organizations/{organization['id']}/employees/{employee_id}/process-assignments"
                ).status_code == 200
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_full_demo_seed_is_repeatable_and_produces_schedulable_pods(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False,
    )
    monkeypatch.setattr(seed_demo_data, "SessionLocal", session_factory)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            created = client.post("/organizations", json={"name": "DataStraw Operations"})
            assert created.status_code == 201

        seed_demo_data.main()
        seed_demo_data.main()

        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            organization_id = 1
            pods = client.get(f"/organizations/{organization_id}/pods").json()
            brands = client.get(f"/organizations/{organization_id}/brands").json()
            processes = client.get(f"/organizations/{organization_id}/processes").json()
            employees = client.get(f"/organizations/{organization_id}/employees").json()
            assert len(pods) == 3
            assert len(brands) == 6
            assert len(processes) == 12
            assert len(employees) == 30
            assert all(employee["is_active"] for employee in employees)

            for pod in pods:
                assert pod["lead_employee_id"] is not None
                lead = next(employee for employee in employees if employee["id"] == pod["lead_employee_id"])
                assert lead["pod_id"] == pod["id"] and lead["is_active"]
                generated = client.post(
                    f"/organizations/{organization_id}/rosters/generate",
                    json={"pod_id": pod["id"], "year": 2026, "month": 12,
                          "solver_time_limit_seconds": 8},
                )
                assert generated.status_code == 201, generated.text
                roster = generated.json()
                assert roster["success"] is True
                assert len(roster["assignments"]) == 120
                report = client.post(
                    f"/organizations/{organization_id}/rosters/{roster['roster_id']}/validate"
                )
                assert report.status_code == 200
                assert report.json()["valid"] is True
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_original_customer_support_seed_scope_keeps_existing_december_demand(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_demo_data, "SessionLocal", session_factory)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            organization = client.post(
                "/organizations", json={"name": "DataStraw Operations"},
            ).json()
            pod = client.post(
                f"/organizations/{organization['id']}/pods", json={"name": "Customer Support"},
            ).json()
            brand = client.post(
                f"/organizations/{organization['id']}/brands",
                json={"pod_id": pod["id"], "name": "Northstar"},
            ).json()
            process = client.post(
                f"/organizations/{organization['id']}/processes",
                json={"brand_id": brand["id"], "name": "CS"},
            ).json()
            employee = client.post(
                f"/organizations/{organization['id']}/employees",
                json={"pod_id": pod["id"], "full_name": "Rahul Sharma", "email": "rahul@example.test"},
            ).json()
            assignment = client.post(
                f"/organizations/{organization['id']}/employees/{employee['id']}/process-assignments",
                json={"process_id": process["id"], "effective_from": "2026-01-01"},
            )
            requirement = client.post(
                f"/organizations/{organization['id']}/staffing-requirements",
                json={"brand_id": brand["id"], "process_id": process["id"],
                      "minimum_staff": 1, "effective_from": "2026-01-01"},
            )
            assert assignment.status_code == requirement.status_code == 201

            ensure_original_pod_demo_demand(
                client, organization["id"], pod, process,
            )

            assignments = client.get(
                f"/organizations/{organization['id']}/employees/{employee['id']}/process-assignments",
            ).json()
            requirements = client.get(
                f"/organizations/{organization['id']}/staffing-requirements",
                params={"process_id": process["id"]},
            ).json()
            assert len(assignments) == 1
            assert len(requirements) == 1
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
