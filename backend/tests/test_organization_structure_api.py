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
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


def create_organization(client: TestClient, name: str) -> dict:
    response = client.post("/organizations", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_organization_structure_crud_end_to_end(client: TestClient) -> None:
    organization = create_organization(client, "Northwind")
    other_organization = create_organization(client, "Contoso")

    listed_organizations = client.get("/organizations").json()
    assert [item["name"] for item in listed_organizations] == ["Northwind", "Contoso"]
    assert client.get(f"/organizations/{organization['id']}").json()["name"] == "Northwind"
    updated_organization = client.put(
        f"/organizations/{organization['id']}", json={"name": "Northwind Retail"}
    )
    assert updated_organization.status_code == 200
    assert updated_organization.json()["name"] == "Northwind Retail"
    assert "created_at" in updated_organization.json()

    pod_response = client.post(
        f"/organizations/{organization['id']}/pods", json={"name": "Central"}
    )
    assert pod_response.status_code == 201
    pod = pod_response.json()
    assert pod["organization_id"] == organization["id"]
    assert client.get(f"/organizations/{organization['id']}/pods").json() == [pod]
    assert client.get(f"/organizations/{organization['id']}/pods/{pod['id']}").json() == pod
    updated_pod = client.put(
        f"/organizations/{organization['id']}/pods/{pod['id']}", json={"name": "East"}
    )
    assert updated_pod.status_code == 200
    assert updated_pod.json()["name"] == "East"

    other_pod = client.post(
        f"/organizations/{other_organization['id']}/pods", json={"name": "Central"}
    ).json()
    brand_response = client.post(
        f"/organizations/{organization['id']}/brands",
        json={"pod_id": pod["id"], "name": "Fresh Foods"},
    )
    assert brand_response.status_code == 201
    brand = brand_response.json()
    assert brand["organization_id"] == organization["id"]
    assert brand["pod_id"] == pod["id"]
    assert client.get(f"/organizations/{organization['id']}/brands").json() == [brand]
    assert client.get(f"/organizations/{organization['id']}/brands/{brand['id']}").json() == brand
    updated_brand = client.put(
        f"/organizations/{organization['id']}/brands/{brand['id']}",
        json={"pod_id": pod["id"], "name": "Fresh Market"},
    )
    assert updated_brand.status_code == 200
    assert updated_brand.json()["name"] == "Fresh Market"
    assert updated_brand.json()["pod_id"] != other_pod["id"]

    process_response = client.post(
        f"/organizations/{organization['id']}/processes",
        json={"brand_id": brand["id"], "name": "Retail", "timezone": "Asia/Kolkata"},
    )
    assert process_response.status_code == 201
    process = process_response.json()
    assert process["organization_id"] == organization["id"]
    assert process["pod_id"] == pod["id"]
    assert process["brand_id"] == brand["id"]
    assert process["timezone"] == "Asia/Kolkata"
    assert client.get(f"/organizations/{organization['id']}/processes").json() == [process]
    assert client.get(f"/organizations/{organization['id']}/processes/{process['id']}").json() == process
    updated_process = client.put(
        f"/organizations/{organization['id']}/processes/{process['id']}",
        json={"brand_id": brand["id"], "name": "Store Operations", "timezone": "UTC"},
    )
    assert updated_process.status_code == 200
    assert updated_process.json()["name"] == "Store Operations"
    assert updated_process.json()["timezone"] == "UTC"


def test_child_queries_and_creates_enforce_organization_ownership(client: TestClient) -> None:
    org_a = create_organization(client, "Org A")
    org_b = create_organization(client, "Org B")
    pod_a = client.post(f"/organizations/{org_a['id']}/pods", json={"name": "Pod A"}).json()
    pod_b = client.post(f"/organizations/{org_b['id']}/pods", json={"name": "Pod B"}).json()
    brand_b = client.post(
        f"/organizations/{org_b['id']}/brands", json={"pod_id": pod_b["id"], "name": "Brand B"}
    ).json()
    process_b = client.post(
        f"/organizations/{org_b['id']}/processes", json={"brand_id": brand_b["id"], "name": "Process B"}
    ).json()

    assert client.get(f"/organizations/{org_a['id']}/pods").json() == [pod_a]
    assert client.get(f"/organizations/{org_a['id']}/brands").json() == []
    assert client.get(f"/organizations/{org_a['id']}/processes").json() == []
    assert client.post(
        f"/organizations/{org_a['id']}/brands", json={"pod_id": pod_b["id"], "name": "Cross Tenant"}
    ).status_code == 404
    assert client.post(
        f"/organizations/{org_a['id']}/processes", json={"brand_id": brand_b["id"], "name": "Cross Tenant"}
    ).status_code == 404
    assert client.get(f"/organizations/{org_a['id']}/pods/{pod_b['id']}").status_code == 404
    assert client.get(f"/organizations/{org_a['id']}/brands/{brand_b['id']}").status_code == 404
    assert client.get(f"/organizations/{org_a['id']}/processes/{process_b['id']}").status_code == 404
    assert client.put(
        f"/organizations/{org_a['id']}/pods/{pod_b['id']}", json={"name": "Relocated"}
    ).status_code == 404
    assert client.put(
        f"/organizations/{org_a['id']}/brands/{brand_b['id']}",
        json={"pod_id": pod_a["id"], "name": "Relocated"},
    ).status_code == 404
    assert client.put(
        f"/organizations/{org_a['id']}/processes/{process_b['id']}",
        json={"brand_id": brand_b["id"], "name": "Relocated"},
    ).status_code == 404
    assert pod_a["organization_id"] == org_a["id"]
    assert client.get(f"/organizations/{org_b['id']}/brands/{brand_b['id']}").json()["pod_id"] == pod_b["id"]
    assert client.get(f"/organizations/{org_b['id']}/processes/{process_b['id']}").json()["pod_id"] == pod_b["id"]


def test_process_pod_is_derived_from_selected_brand_on_update(client: TestClient) -> None:
    organization = create_organization(client, "Org")
    pod_one = client.post(f"/organizations/{organization['id']}/pods", json={"name": "One"}).json()
    pod_two = client.post(f"/organizations/{organization['id']}/pods", json={"name": "Two"}).json()
    brand_one = client.post(
        f"/organizations/{organization['id']}/brands",
        json={"pod_id": pod_one["id"], "name": "Brand One"},
    ).json()
    brand_two = client.post(
        f"/organizations/{organization['id']}/brands",
        json={"pod_id": pod_two["id"], "name": "Brand Two"},
    ).json()
    process = client.post(
        f"/organizations/{organization['id']}/processes",
        json={"brand_id": brand_one["id"], "name": "Process"},
    ).json()

    response = client.put(
        f"/organizations/{organization['id']}/processes/{process['id']}",
        json={"brand_id": brand_two["id"], "name": "Process", "timezone": "UTC"},
    )

    assert response.status_code == 200
    assert response.json()["organization_id"] == organization["id"]
    assert response.json()["brand_id"] == brand_two["id"]
    assert response.json()["pod_id"] == pod_two["id"]


def test_brand_move_with_process_is_conflict_and_transaction_rolls_back(client: TestClient) -> None:
    organization = create_organization(client, "Org")
    pod_one = client.post(f"/organizations/{organization['id']}/pods", json={"name": "One"}).json()
    pod_two = client.post(f"/organizations/{organization['id']}/pods", json={"name": "Two"}).json()
    brand = client.post(
        f"/organizations/{organization['id']}/brands",
        json={"pod_id": pod_one["id"], "name": "Brand"},
    ).json()
    client.post(
        f"/organizations/{organization['id']}/processes",
        json={"brand_id": brand["id"], "name": "Process"},
    )

    response = client.put(
        f"/organizations/{organization['id']}/brands/{brand['id']}",
        json={"pod_id": pod_two["id"], "name": "Brand"},
    )

    assert response.status_code == 409
    assert "duplicate" not in response.json()["detail"].lower()
    persisted = client.get(f"/organizations/{organization['id']}/brands/{brand['id']}").json()
    assert persisted["pod_id"] == pod_one["id"]


def test_put_rejects_attempted_organization_ownership_changes(client: TestClient) -> None:
    organization = create_organization(client, "Org")
    pod = client.post(f"/organizations/{organization['id']}/pods", json={"name": "Pod"}).json()
    brand = client.post(
        f"/organizations/{organization['id']}/brands",
        json={"pod_id": pod["id"], "name": "Brand"},
    ).json()
    process = client.post(
        f"/organizations/{organization['id']}/processes",
        json={"brand_id": brand["id"], "name": "Process"},
    ).json()

    assert client.put(
        f"/organizations/{organization['id']}/pods/{pod['id']}",
        json={"name": "Pod", "organization_id": organization["id"] + 1},
    ).status_code == 422
    assert client.put(
        f"/organizations/{organization['id']}/brands/{brand['id']}",
        json={"pod_id": pod["id"], "name": "Brand", "organization_id": organization["id"] + 1},
    ).status_code == 422
    assert client.put(
        f"/organizations/{organization['id']}/processes/{process['id']}",
        json={"brand_id": brand["id"], "name": "Process", "organization_id": organization["id"] + 1},
    ).status_code == 422


def test_duplicate_names_return_conflict(client: TestClient) -> None:
    organization = create_organization(client, "Org")
    pod = client.post(f"/organizations/{organization['id']}/pods", json={"name": "Pod"}).json()
    assert client.post(
        f"/organizations/{organization['id']}/pods", json={"name": "Pod"}
    ).status_code == 409

    brand_data = {"pod_id": pod["id"], "name": "Brand"}
    brand = client.post(f"/organizations/{organization['id']}/brands", json=brand_data).json()
    assert client.post(f"/organizations/{organization['id']}/brands", json=brand_data).status_code == 409

    process_data = {"brand_id": brand["id"], "name": "Process"}
    assert client.post(f"/organizations/{organization['id']}/processes", json=process_data).status_code == 201
    assert client.post(f"/organizations/{organization['id']}/processes", json=process_data).status_code == 409


def test_invalid_ids_payloads_and_openapi(client: TestClient) -> None:
    assert client.get("/organizations/999999").status_code == 404
    assert client.get("/organizations/0").status_code == 404
    assert client.post("/organizations", json={"name": "   "}).status_code == 422
    assert client.post(
        "/organizations/1/pods", json={"name": "Invalid", "organization_id": 100}
    ).status_code == 422
    assert client.post("/organizations/1/brands", json={"pod_id": -1, "name": "Invalid"}).status_code == 422

    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/organizations" in paths
    assert "/organizations/{organization_id}/pods" in paths
    assert "/organizations/{organization_id}/brands" in paths
    assert "/organizations/{organization_id}/processes" in paths
    assert "/health" in paths
