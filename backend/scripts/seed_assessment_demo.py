"""Create the PDF-shaped sample dataset in a dedicated, empty database only.

Run from backend/: python -m scripts.seed_assessment_demo
The script refuses to run if the configured database already contains any
Organization, so it cannot seed over the populated development database.
"""

from sqlalchemy import func, select
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.models import Employee, Organization
from scripts.authenticated_client import authenticated_test_client


EMPLOYEE_NAMES = [
    "Aarav Mehta", "Ananya Iyer", "Arjun Nair", "Diya Kapoor", "Ishaan Rao",
    "Kavya Menon", "Kabir Shah", "Meera Joshi", "Neha Verma", "Rohan Das",
    "Sana Khan", "Vihaan Patel", "Aditi Bose", "Dev Malhotra", "Ira Kulkarni",
    "Karan Sethi", "Mira Reddy", "Nikhil Jain", "Pooja Sen", "Reyansh Gupta",
    "Sia Thomas", "Tara Bhat", "Uday Kumar", "Vanya Roy", "Yash Arora",
    "Zoya Fernandes", "Advik Chawla", "Bhavna Pillai", "Chirag Shetty", "Esha Dutta",
]
POD_SPECS = (
    ("Customer Care", ("solo", "solo", "solo", "solo", "solo", "solo", "shared2", "shared3")),
    ("Digital Support", ("solo", "solo", "solo", "solo", "solo", "solo", "shared2", "shared3")),
    ("Back Office", ("solo", "solo", "solo", "solo", "solo", "solo", "shared2")),
)


def require(response, expected: int):
    if response.status_code != expected:
        raise RuntimeError(
            f"{response.request.method} {response.request.url}: expected {expected}, "
            f"received {response.status_code}: {response.text}"
        )
    return response.json() if response.content else None


def _calendar_rules(open_days: set[int], opens_at: str, closes_at: str) -> list[dict]:
    return [{
        "day_of_week": weekday,
        "sequence": 1,
        "is_open": weekday in open_days,
        "opens_at": opens_at if weekday in open_days else None,
        "closes_at": closes_at if weekday in open_days else None,
        "effective_from": "2026-01-01",
    } for weekday in range(7)]


def seed_assessment_demo(client: TestClient, organization_id: int) -> dict:
    """Populate a new org with 30 employees, 23 brands, and a 78/22 split."""
    pod_rows = {}
    for pod_name, _ in POD_SPECS:
        pod_rows[pod_name] = require(client.post(
            f"/organizations/{organization_id}/pods", json={"name": pod_name},
        ), 201)

    holiday = require(client.post(f"/organizations/{organization_id}/holidays", json={
        "holiday_date": "2026-12-25", "name": "Christmas Day", "region": "India",
    }), 201)

    employee_index = 0
    brand_index = 0
    shared2_owners_by_pod: dict[int, list[int]] = {}
    shared2_core_process_by_pod: dict[int, dict] = {}
    brand_distribution: list[tuple[int, str, list[int]]] = []
    process_rows: list[dict] = []

    for pod_name, brand_kinds in POD_SPECS:
        pod_id = pod_rows[pod_name]["id"]
        for kind in brand_kinds:
            brand_index += 1
            brand_name = f"Assessment Brand {brand_index:02d}"
            brand = require(client.post(f"/organizations/{organization_id}/brands", json={
                "pod_id": pod_id, "name": brand_name,
            }), 201)
            team_size = 1 if kind == "solo" else (2 if kind == "shared2" else 3)
            team_ids = []
            for _ in range(team_size):
                employee_index += 1
                employee = require(client.post(f"/organizations/{organization_id}/employees", json={
                    "pod_id": pod_id,
                    "full_name": EMPLOYEE_NAMES[employee_index - 1],
                    "email": f"assessment.employee{employee_index:02d}@datastraw.test",
                    "role": "Associate",
                    "is_active": True,
                }), 201)
                team_ids.append(employee["id"])

            brand_processes = []
            process_count = 1 if kind == "solo" else 2
            for process_index in range(process_count):
                process = require(client.post(f"/organizations/{organization_id}/processes", json={
                    "brand_id": brand["id"],
                    "name": "Core Operations" if process_index == 0 else "Specialist Operations",
                    "timezone": "Asia/Kolkata",
                }), 201)
                brand_processes.append(process)

                if kind == "solo":
                    open_days = set(range(6))  # Monday through Saturday; the employee is off Sunday.
                    opens_at, closes_at = "09:00:00", "17:00:00"
                    minimum = 1
                else:
                    open_days = set(range(7)) if process_index == 0 else set(range(6))
                    opens_at, closes_at = (
                        ("09:00:00", "13:00:00") if process_index == 0
                        else ("13:00:00", "17:00:00")
                    )
                    minimum = 2 if kind == "shared3" and process_index == 0 else 1

                require(client.put(
                    f"/organizations/{organization_id}/processes/{process['id']}/calendar",
                    json={"timezone": "Asia/Kolkata", "rules": _calendar_rules(
                        open_days, opens_at, closes_at,
                    )},
                ), 200)
                if kind != "solo" and process_index == 0:
                    require(client.post(
                        f"/organizations/{organization_id}/processes/{process['id']}/holiday-overrides",
                        json={"holiday_id": holiday["id"], "sequence": 1, "is_open": True,
                              "opens_at": opens_at, "closes_at": closes_at},
                    ), 201)
                require(client.post(f"/organizations/{organization_id}/staffing-requirements", json={
                    "brand_id": brand["id"], "process_id": process["id"],
                    "minimum_staff": minimum, "effective_from": "2026-01-01",
                }), 201)

            assignment_team_ids = team_ids
            # A 365-day shared process includes Sunday and the public holiday.
            # Its holiday override opens process 1; process 2 and solo processes
            # retain the default holiday closure.
            if kind == "solo":
                require(client.post(
                    f"/organizations/{organization_id}/employees/{team_ids[0]}/availability",
                    json={"day_of_week": 6, "sequence": 1, "is_available": False,
                          "effective_from": "2026-01-01"},
                ), 201)
                eligible_processes = [(team_ids[0], process) for process in brand_processes]
            elif kind == "shared2":
                shared2_owners_by_pod[pod_id] = team_ids
                shared2_core_process_by_pod[pod_id] = brand_processes[0]
                eligible_processes = [
                    (team_ids[0], brand_processes[0]),
                    (team_ids[0], brand_processes[1]),
                    (team_ids[1], brand_processes[0]),
                ]
            else:
                # The shared-brand owner also covers a process at the other
                # shared brand, demonstrating cross-brand eligibility without
                # changing either brand's 2-3 employee distribution.
                shared2_owners = shared2_owners_by_pod.get(pod_id)
                if shared2_owners:
                    assignment_team_ids = [shared2_owners[0], team_ids[0], team_ids[1]]
                eligible_processes = [
                    (assignment_team_ids[0], brand_processes[0]),
                    (assignment_team_ids[0], brand_processes[1]),
                    (assignment_team_ids[1], brand_processes[0]),
                    (assignment_team_ids[2], brand_processes[1]),
                ]
                if shared2_owners:
                    # The shared-brand owner replaces the third newly created
                    # employee in this brand's three-person assignment team.
                    # Keep that employee distributed in the same Pod by adding
                    # them to the preceding shared brand's Core Operations.
                    eligible_processes.append((
                        team_ids[2], shared2_core_process_by_pod[pod_id],
                    ))

            for employee_id, process in eligible_processes:
                require(client.post(
                    f"/organizations/{organization_id}/employees/{employee_id}/process-assignments",
                    json={"process_id": process["id"], "effective_from": "2026-01-01",
                          "effective_to": "2026-12-31"},
                ), 201)
            brand_distribution.append((brand["id"], kind, assignment_team_ids))
            process_rows.extend(brand_processes)

    # Do not assign or infer Pod Leads; users choose them from the application.
    return {
        "pods": list(pod_rows.values()),
        "brands": brand_distribution,
        "processes": process_rows,
        "employees_created": employee_index,
        "holiday": holiday,
    }


def main() -> None:
    with SessionLocal() as session:
        organization_count = session.scalar(select(func.count(Organization.id))) or 0
        if organization_count:
            raise RuntimeError(
                "Refusing to seed a non-empty database. Configure a separate, empty "
                "assessment DATABASE_URL first."
            )

    with authenticated_test_client() as client:
        organization = require(client.post("/organizations", json={
            "name": "DataStraw Assessment Demo",
        }), 201)
        summary = seed_assessment_demo(client, organization["id"])
    print(
        f"Created Organization {organization['id']} with {summary['employees_created']} employees, "
        f"{len(summary['brands'])} brands (18 single-owner, 5 shared), "
        f"{len(summary['processes'])} processes, and {len(summary['pods'])} Pods. "
        "No Pod Leads were assigned."
    )


if __name__ == "__main__":
    main()
