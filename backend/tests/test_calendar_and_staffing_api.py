from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.db.base import Base
from app.main import app
from auth_support import TEST_AUTH_HEADERS


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def setup_process(client: TestClient, org_name: str = "Org", pod_name: str = "Pod") -> tuple[dict, dict, dict, dict]:
    organization = client.post("/organizations", json={"name": org_name}).json()
    pod = client.post(f"/organizations/{organization['id']}/pods", json={"name": pod_name}).json()
    brand = client.post(
        f"/organizations/{organization['id']}/brands", json={"pod_id": pod["id"], "name": "Brand"}
    ).json()
    process = client.post(
        f"/organizations/{organization['id']}/processes",
        json={"brand_id": brand["id"], "name": "Process"},
    ).json()
    return organization, pod, brand, process


def test_operating_calendar_configures_365_day_operations_timezone_and_closures(client: TestClient) -> None:
    org, _, _, process = setup_process(client)
    endpoint = f"/organizations/{org['id']}/processes/{process['id']}/calendar"
    rules = [
        {
            "day_of_week": weekday,
            "sequence": 1,
            "is_open": True,
            "opens_at": "08:00:00",
            "closes_at": "20:00:00",
            "effective_from": "2026-01-01",
        }
        for weekday in range(7)
    ]
    updated = client.put(endpoint, json={"timezone": "Asia/Kolkata", "rules": rules})
    assert updated.status_code == 200
    assert updated.json()["timezone"] == "Asia/Kolkata"
    assert len(updated.json()["rules"]) == 7
    days = client.get(
        f"{endpoint}/effective?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    assert len(days) == 365
    assert all(day["is_open"] for day in days)

    sunday_close = {
        "day_of_week": 6,
        "sequence": 1,
        "is_open": False,
        "effective_from": "2026-01-01",
    }
    assert client.put(endpoint, json={"timezone": "Asia/Kolkata", "rules": [sunday_close]}).status_code == 200
    sunday = client.get(
        f"{endpoint}/effective?start_date=2026-01-04&end_date=2026-01-04"
    ).json()[0]
    assert sunday["source"] == "operating_rule"
    assert sunday["is_open"] is False
    assert client.put(endpoint, json={"timezone": "Not/A_Zone", "rules": []}).status_code == 422


def test_process_without_calendar_rules_defaults_to_closed(client: TestClient) -> None:
    org, _, _, process = setup_process(client)
    endpoint = f"/organizations/{org['id']}/processes/{process['id']}/calendar/effective"

    response = client.get(endpoint, params={"start_date": "2026-04-01", "end_date": "2026-04-01"})

    assert response.status_code == 200
    assert response.json() == [{
        "operating_date": "2026-04-01", "is_holiday": False,
        "source": "closed_default", "is_open": False, "sequence": 1,
        "opens_at": None, "closes_at": None, "reason": "No operating rule configured",
    }]


def test_weekly_and_date_overrides_precedence_and_effective_range(client: TestClient) -> None:
    org, _, _, process = setup_process(client)
    calendar_url = f"/organizations/{org['id']}/processes/{process['id']}/calendar"
    closed_sunday = {
        "day_of_week": 6,
        "sequence": 1,
        "is_open": False,
        "effective_from": "2026-01-01",
    }
    assert client.put(calendar_url, json={"timezone": "UTC", "rules": [closed_sunday]}).status_code == 200
    day_override_url = f"/organizations/{org['id']}/processes/{process['id']}/day-overrides"
    opened = client.post(
        day_override_url,
        json={
            "day_of_week": 6,
            "sequence": 1,
            "is_open": True,
            "opens_at": "10:00:00",
            "closes_at": "16:00:00",
            "effective_from": "2026-02-01",
            "reason": "Seasonal Sunday opening",
        },
    )
    assert opened.status_code == 201
    effective_sunday = client.get(
        f"{calendar_url}/effective?start_date=2026-02-01&end_date=2026-02-01"
    ).json()[0]
    assert effective_sunday["source"] == "day_override"
    assert effective_sunday["is_open"] is True

    date_url = f"/organizations/{org['id']}/processes/{process['id']}/date-overrides"
    one_off = client.post(
        date_url,
        json={"override_date": "2026-02-01", "sequence": 1, "is_open": False, "reason": "Emergency closure"},
    )
    assert one_off.status_code == 201
    effective = client.get(
        f"{calendar_url}/effective?start_date=2026-02-01&end_date=2026-02-01"
    ).json()[0]
    assert effective["source"] == "date_override"
    assert effective["is_open"] is False
    assert client.get(f"{date_url}?start_date=2026-02-01&end_date=2026-02-28").json() == [one_off.json()]
    assert client.get(f"{date_url}?start_date=2026-03-01&end_date=2026-02-28").status_code == 422


def test_calendar_rule_overlap_and_update_are_handled(client: TestClient) -> None:
    org, _, _, process = setup_process(client)
    endpoint = f"/organizations/{org['id']}/processes/{process['id']}/calendar"
    base = {
        "day_of_week": 1,
        "sequence": 1,
        "is_open": True,
        "opens_at": "09:00:00",
        "closes_at": "17:00:00",
        "effective_from": "2026-01-01",
        "effective_to": "2026-03-01",
    }
    assert client.put(endpoint, json={"timezone": "UTC", "rules": [base]}).status_code == 200
    overlapping = {**base, "effective_from": "2026-02-01"}
    assert client.put(endpoint, json={"timezone": "UTC", "rules": [overlapping]}).status_code == 409
    edited = {**base, "opens_at": "10:00:00"}
    result = client.put(endpoint, json={"timezone": "UTC", "rules": [edited]})
    assert result.status_code == 200
    assert result.json()["rules"][0]["opens_at"] == "10:00:00"


def test_holidays_are_unique_by_organization_date_and_support_process_overrides(client: TestClient) -> None:
    org, _, _, process = setup_process(client)
    other_org, _, _, _ = setup_process(client, "Other Org", "Other Pod")
    holiday_url = f"/organizations/{org['id']}/holidays"
    holiday = client.post(
        holiday_url,
        json={"holiday_date": "2026-12-25", "name": "Winter Holiday", "region": "North"},
    )
    assert holiday.status_code == 201
    assert client.post(
        holiday_url, json={"holiday_date": "2026-12-25", "name": "Different name"}
    ).status_code == 409
    assert client.get(f"{holiday_url}?start_date=2026-12-01&end_date=2026-12-31").json() == [holiday.json()]
    effective_url = f"/organizations/{org['id']}/processes/{process['id']}/calendar/effective"
    holiday_day = client.get(f"{effective_url}?start_date=2026-12-25&end_date=2026-12-25").json()[0]
    assert holiday_day["is_holiday"] is True
    assert holiday_day["is_open"] is False

    holiday_override_url = f"/organizations/{org['id']}/processes/{process['id']}/holiday-overrides"
    override = client.post(
        holiday_override_url,
        json={"holiday_id": holiday.json()["id"], "sequence": 1, "is_open": True, "opens_at": "10:00:00", "closes_at": "14:00:00"},
    )
    assert override.status_code == 201
    opened = client.get(f"{effective_url}?start_date=2026-12-25&end_date=2026-12-25").json()[0]
    assert opened["source"] == "holiday_override"
    assert opened["is_open"] is True
    foreign_holiday = client.post(
        f"/organizations/{other_org['id']}/holidays",
        json={"holiday_date": "2026-12-26", "name": "Foreign"},
    ).json()
    assert client.post(
        holiday_override_url,
        json={"holiday_id": foreign_holiday["id"], "is_open": False},
    ).status_code == 404

    updated = client.put(
        f"{holiday_url}/{holiday.json()['id']}",
        json={"holiday_date": "2026-12-24", "name": "Winter Eve", "region": None},
    )
    assert updated.status_code == 200
    assert client.delete(f"{holiday_url}/{holiday.json()['id']}").status_code == 204
    assert client.get(f"{holiday_url}/{holiday.json()['id']}").status_code == 404


def test_staffing_requirements_cover_brand_process_weekdays_and_dates(client: TestClient) -> None:
    org, _, brand, process = setup_process(client)
    endpoint = f"/organizations/{org['id']}/staffing-requirements"
    brand_rule = client.post(
        endpoint,
        json={"brand_id": brand["id"], "day_of_week": 0, "minimum_staff": 3, "effective_from": "2026-01-01"},
    )
    process_rule = client.post(
        endpoint,
        json={"brand_id": brand["id"], "process_id": process["id"], "day_of_week": 0, "minimum_staff": 2, "effective_from": "2026-01-01", "effective_to": "2026-01-31"},
    )
    date_override = client.post(
        endpoint,
        json={"brand_id": brand["id"], "process_id": process["id"], "override_date": "2026-01-05", "minimum_staff": 5},
    )
    assert brand_rule.status_code == process_rule.status_code == date_override.status_code == 201
    assert date_override.json()["effective_from"] == "2026-01-05"
    assert len(client.get(f"{endpoint}?brand_id={brand['id']}&start_date=2026-01-01&end_date=2026-01-31").json()) == 3
    process_filtered = client.get(f"{endpoint}?process_id={process['id']}").json()
    assert {row["id"] for row in process_filtered} == {process_rule.json()["id"], date_override.json()["id"]}

    assert client.post(
        endpoint,
        json={"brand_id": brand["id"], "process_id": process["id"], "day_of_week": 0, "minimum_staff": 4, "effective_from": "2026-02-01"},
    ).status_code == 201
    assert client.post(
        endpoint,
        json={"brand_id": brand["id"], "process_id": process["id"], "day_of_week": 0, "minimum_staff": 4, "effective_from": "2026-02-15"},
    ).status_code == 409

    changed = client.put(
        f"{endpoint}/{brand_rule.json()['id']}",
        json={"brand_id": brand["id"], "day_of_week": 0, "minimum_staff": 4, "effective_from": "2026-01-01"},
    )
    assert changed.status_code == 200
    assert changed.json()["minimum_staff"] == 4
    assert client.delete(f"{endpoint}/{date_override.json()['id']}").status_code == 204


def test_staffing_relationships_and_cross_organization_access_are_validated(client: TestClient) -> None:
    org_a, _, brand_a, process_a = setup_process(client, "A", "A pod")
    org_b, _, brand_b, process_b = setup_process(client, "B", "B pod")
    brand_other = client.post(
        f"/organizations/{org_a['id']}/brands",
        json={"pod_id": brand_a["pod_id"], "name": "Other brand in A"},
    ).json()
    endpoint = f"/organizations/{org_a['id']}/staffing-requirements"
    assert client.post(
        endpoint,
        json={"brand_id": brand_b["id"], "minimum_staff": 1, "effective_from": "2026-01-01"},
    ).status_code == 404
    assert client.post(
        endpoint,
        json={"brand_id": brand_a["id"], "process_id": process_b["id"], "minimum_staff": 1, "effective_from": "2026-01-01"},
    ).status_code == 404
    assert client.post(
        endpoint,
        json={"brand_id": brand_other["id"], "process_id": process_a["id"], "minimum_staff": 1, "effective_from": "2026-01-01"},
    ).status_code == 422
