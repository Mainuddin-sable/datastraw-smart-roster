from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.db.base import Base
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def create_org(client: TestClient, name: str) -> dict:
    response = client.post("/organizations", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_pod(client: TestClient, org_id: int, name: str) -> dict:
    response = client.post(f"/organizations/{org_id}/pods", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_brand_and_process(client: TestClient, org_id: int, pod_id: int, name: str) -> tuple[dict, dict]:
    brand_response = client.post(
        f"/organizations/{org_id}/brands", json={"pod_id": pod_id, "name": f"{name} brand"}
    )
    assert brand_response.status_code == 201
    brand = brand_response.json()
    process_response = client.post(
        f"/organizations/{org_id}/processes",
        json={"brand_id": brand["id"], "name": f"{name} process"},
    )
    assert process_response.status_code == 201
    return brand, process_response.json()


def create_employee(client: TestClient, org_id: int, pod_id: int, email: str = "person@example.test") -> dict:
    response = client.post(
        f"/organizations/{org_id}/employees",
        json={"pod_id": pod_id, "full_name": "Taylor Employee", "email": email},
    )
    assert response.status_code == 201
    return response.json()


def test_employee_crud_status_and_case_insensitive_org_email_uniqueness(client: TestClient) -> None:
    org = create_org(client, "Org")
    pod = create_pod(client, org["id"], "Pod")
    employee = create_employee(client, org["id"], pod["id"], "Person@Example.Test")

    assert employee["email"] == "person@example.test"
    assert employee["is_active"] is True
    assert client.get(f"/organizations/{org['id']}/employees").json() == [employee]
    assert client.get(f"/organizations/{org['id']}/employees?is_active=false").json() == []
    assert client.post(
        f"/organizations/{org['id']}/employees",
        json={"pod_id": pod["id"], "full_name": "Duplicate", "email": "PERSON@example.test"},
    ).status_code == 409

    updated = client.put(
        f"/organizations/{org['id']}/employees/{employee['id']}",
        json={
            "full_name": "Taylor Updated",
            "email": "updated@example.test",
            "role": "Lead",
            "is_active": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False
    assert client.get(f"/organizations/{org['id']}/employees?is_active=false").json() == [updated.json()]


def test_employee_invalid_and_cross_organization_ids_are_scoped(client: TestClient) -> None:
    org_a = create_org(client, "A")
    org_b = create_org(client, "B")
    pod_a = create_pod(client, org_a["id"], "Pod A")
    pod_b = create_pod(client, org_b["id"], "Pod B")
    employee_b = create_employee(client, org_b["id"], pod_b["id"])

    assert client.post(
        f"/organizations/{org_a['id']}/employees",
        json={"pod_id": pod_b["id"], "full_name": "Invalid", "email": "invalid@example.test"},
    ).status_code == 404
    assert client.get(f"/organizations/{org_a['id']}/employees/{employee_b['id']}").status_code == 404
    assert client.put(
        f"/organizations/{org_a['id']}/employees/{employee_b['id']}",
        json={"full_name": "Changed", "email": "changed@example.test", "role": None, "is_active": False},
    ).status_code == 404
    assert client.get(f"/organizations/{org_a['id']}/employees/0").status_code == 422
    assert client.post(
        f"/organizations/{org_a['id']}/employees",
        json={"pod_id": pod_a["id"], "full_name": "Bad Email", "email": "not-an-email"},
    ).status_code == 422


def test_process_eligibility_supports_multiple_brands_and_effective_windows(client: TestClient) -> None:
    org = create_org(client, "Org")
    pod = create_pod(client, org["id"], "Pod")
    _, process_a = create_brand_and_process(client, org["id"], pod["id"], "A")
    brand_b, process_b = create_brand_and_process(client, org["id"], pod["id"], "B")
    employee = create_employee(client, org["id"], pod["id"])

    first = client.post(
        f"/organizations/{org['id']}/employees/{employee['id']}/process-assignments",
        json={"process_id": process_a["id"], "effective_from": "2026-01-01", "effective_to": "2026-01-10"},
    )
    second = client.post(
        f"/organizations/{org['id']}/employees/{employee['id']}/process-assignments",
        json={"process_id": process_b["id"], "effective_from": "2026-01-01"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    eligible = client.get(
        f"/organizations/{org['id']}/employees/{employee['id']}/process-assignments"
    ).json()
    assert {row["brand_id"] for row in eligible} == {process_a["brand_id"], brand_b["id"]}

    updated = client.put(
        f"/organizations/{org['id']}/employees/{employee['id']}/process-assignments/{first.json()['id']}",
        json={"process_id": process_a["id"], "effective_from": "2026-01-01", "effective_to": "2026-01-09"},
    )
    assert updated.status_code == 200
    deactivated = client.post(
        f"/organizations/{org['id']}/employees/{employee['id']}/process-assignments/{first.json()['id']}/deactivate",
        json={"effective_to": "2026-01-08"},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["effective_to"] == "2026-01-08"


def test_process_assignment_rejects_overlap_and_invalid_ownership(client: TestClient) -> None:
    org_a = create_org(client, "A")
    org_b = create_org(client, "B")
    pod_a = create_pod(client, org_a["id"], "Pod A")
    pod_a_two = create_pod(client, org_a["id"], "Pod A2")
    pod_b = create_pod(client, org_b["id"], "Pod B")
    _, process_a = create_brand_and_process(client, org_a["id"], pod_a["id"], "A")
    _, process_other_pod = create_brand_and_process(client, org_a["id"], pod_a_two["id"], "A2")
    _, process_b = create_brand_and_process(client, org_b["id"], pod_b["id"], "B")
    employee = create_employee(client, org_a["id"], pod_a["id"])
    endpoint = f"/organizations/{org_a['id']}/employees/{employee['id']}/process-assignments"

    assignment = client.post(
        endpoint,
        json={"process_id": process_a["id"], "effective_from": "2026-03-01", "effective_to": "2026-03-10"},
    )
    assert assignment.status_code == 201
    assert client.post(
        endpoint,
        json={"process_id": process_a["id"], "effective_from": "2026-03-10"},
    ).status_code == 409
    adjacent = client.post(
        endpoint,
        json={"process_id": process_a["id"], "effective_from": "2026-03-11"},
    )
    assert adjacent.status_code == 201
    assert client.put(
        f"{endpoint}/{adjacent.json()['id']}",
        json={"process_id": process_a["id"], "effective_from": "2026-03-10"},
    ).status_code == 409
    assert client.post(
        endpoint,
        json={"process_id": process_b["id"], "effective_from": "2026-04-01"},
    ).status_code == 404
    assert client.post(
        endpoint,
        json={"process_id": process_other_pod["id"], "effective_from": "2026-04-01"},
    ).status_code == 422
    assert client.post(
        endpoint,
        json={"process_id": process_a["id"], "effective_from": "2026-05-02", "effective_to": "2026-05-01"},
    ).status_code == 422


def test_availability_crud_date_range_and_overlap_validation(client: TestClient) -> None:
    org = create_org(client, "Org")
    pod = create_pod(client, org["id"], "Pod")
    employee = create_employee(client, org["id"], pod["id"])
    endpoint = f"/organizations/{org['id']}/employees/{employee['id']}/availability"
    availability = client.post(
        endpoint,
        json={
            "day_of_week": 1,
            "sequence": 1,
            "is_available": True,
            "available_from": "09:00:00",
            "available_to": "17:00:00",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    assert availability.status_code == 201
    assert client.post(
        endpoint,
        json={"day_of_week": 1, "sequence": 1, "effective_from": "2026-06-01"},
    ).status_code == 409
    assert client.get(f"{endpoint}?start_date=2026-06-01&end_date=2026-06-30").json() == [availability.json()]
    assert client.get(f"{endpoint}?start_date=2027-01-01&end_date=2027-01-31").json() == []
    assert client.get(f"{endpoint}?start_date=2026-07-01&end_date=2026-06-30").status_code == 422

    updated = client.put(
        f"{endpoint}/{availability.json()['id']}",
        json={
            "day_of_week": 1,
            "sequence": 1,
            "is_available": True,
            "available_from": "10:00:00",
            "available_to": "18:00:00",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["available_from"] == "10:00:00"

    second_sequence = client.post(
        endpoint,
        json={
            "day_of_week": 1,
            "sequence": 2,
            "is_available": True,
            "available_from": "18:00:00",
            "available_to": "20:00:00",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    assert second_sequence.status_code == 201
    conflicting_update = client.put(
        f"{endpoint}/{second_sequence.json()['id']}",
        json={
            "day_of_week": 1,
            "sequence": 1,
            "is_available": True,
            "available_from": "18:00:00",
            "available_to": "20:00:00",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    assert conflicting_update.status_code == 409
    assert client.get(f"{endpoint}?start_date=2026-06-01&end_date=2026-06-30").json()[1]["sequence"] == 2


def test_availability_and_process_assignment_ids_are_organization_scoped(client: TestClient) -> None:
    org_a = create_org(client, "A")
    org_b = create_org(client, "B")
    pod_a = create_pod(client, org_a["id"], "Pod A")
    pod_b = create_pod(client, org_b["id"], "Pod B")
    employee_a = create_employee(client, org_a["id"], pod_a["id"])
    employee_b = create_employee(client, org_b["id"], pod_b["id"])
    _, process_b = create_brand_and_process(client, org_b["id"], pod_b["id"], "B")
    foreign_assignment = client.post(
        f"/organizations/{org_b['id']}/employees/{employee_b['id']}/process-assignments",
        json={"process_id": process_b["id"], "effective_from": "2026-01-01"},
    ).json()

    assert client.get(
        f"/organizations/{org_a['id']}/employees/{employee_b['id']}/process-assignments"
    ).status_code == 404
    assert client.put(
        f"/organizations/{org_a['id']}/employees/{employee_a['id']}/process-assignments/{foreign_assignment['id']}",
        json={"process_id": process_b["id"], "effective_from": "2026-01-01"},
    ).status_code == 404
    assert client.get(
        f"/organizations/{org_a['id']}/employees/{employee_b['id']}/availability?start_date=2026-01-01&end_date=2026-01-02"
    ).status_code == 404


def test_employee_openapi_includes_employee_management_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/organizations/{organization_id}/employees" in paths
    assert "/organizations/{organization_id}/employees/{employee_id}/process-assignments" in paths
    assert "/organizations/{organization_id}/employees/{employee_id}/availability" in paths
