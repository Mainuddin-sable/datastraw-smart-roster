"""Add repeatable demo scheduling data to the configured development database.

The script only creates missing resources. It preserves all existing records,
including the manually created organization and employees.

Run from backend/: python -m scripts.seed_demo_data
"""

from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import Employee, Organization
from scripts.authenticated_client import authenticated_test_client


def require(response, expected: int):
    if response.status_code != expected:
        raise RuntimeError(f"{response.request.method} {response.request.url}: "
                           f"expected {expected}, received {response.status_code}: {response.text}")
    return response.json() if response.content else None


def ensure_default_calendar(client, organization_id: int, process: dict) -> None:
    """Initialize an unconfigured demo Process with recurring 09:00–17:00 hours."""
    endpoint = f"/organizations/{organization_id}/processes/{process['id']}/calendar"
    calendar = require(client.get(endpoint), 200)
    if calendar["rules"]:
        return
    rules = [{
        "day_of_week": weekday, "sequence": 1, "is_open": True,
        "opens_at": "09:00:00", "closes_at": "17:00:00",
        "effective_from": "2026-01-01",
    } for weekday in range(7)]
    require(client.put(endpoint, json={
        "timezone": process["timezone"], "rules": rules,
    }), 200)


def ensure_process_eligibility(
    client,
    organization_id: int,
    employee_id: int,
    process_id: int,
    start_date: str,
    end_date: str,
    assignments: list[dict] | None = None,
) -> list[dict]:
    """Fill uncovered eligibility dates without overlapping existing periods."""
    rows = assignments if assignments is not None else require(
        client.get(f"/organizations/{organization_id}/employees/{employee_id}/process-assignments"), 200
    )
    period_start = date.fromisoformat(start_date)
    period_end = date.fromisoformat(end_date)
    cursor = period_start
    gaps: list[tuple[date, date]] = []
    relevant = sorted(
        (row for row in rows if row["process_id"] == process_id),
        key=lambda row: row["effective_from"],
    )
    for row in relevant:
        assignment_start = date.fromisoformat(row["effective_from"])
        assignment_end = date.fromisoformat(row["effective_to"]) if row["effective_to"] else None
        if assignment_end is not None and assignment_end < cursor:
            continue
        if assignment_start > period_end:
            break
        if assignment_start > cursor:
            gaps.append((cursor, min(period_end, assignment_start - timedelta(days=1))))
        if assignment_end is None:
            cursor = period_end + timedelta(days=1)
            break
        cursor = max(cursor, assignment_end + timedelta(days=1))
        if cursor > period_end:
            break
    if cursor <= period_end:
        gaps.append((cursor, period_end))

    for gap_start, gap_end in gaps:
        created = require(client.post(
            f"/organizations/{organization_id}/employees/{employee_id}/process-assignments",
            json={"process_id": process_id, "effective_from": gap_start.isoformat(),
                  "effective_to": gap_end.isoformat()},
        ), 201)
        rows.append(created)
    return rows


def ensure_original_pod_demo_demand(client, organization_id: int, pod: dict, process: dict) -> None:
    """Keep Rahul's existing CS assignment and make its future demo period schedulable."""
    with SessionLocal() as session:
        employee = session.scalar(select(Employee).where(
            Employee.organization_id == organization_id,
            Employee.pod_id == pod["id"],
            Employee.full_name == "Rahul Sharma",
        ))
        if employee is None:
            return
        employee_id = employee.id

    ensure_process_eligibility(
        client, organization_id, employee_id, process["id"], "2026-12-01", "2026-12-31"
    )

    requirements_url = f"/organizations/{organization_id}/staffing-requirements"
    requirements = require(client.get(requirements_url, params={"process_id": process["id"]}), 200)
    december_start = date(2026, 12, 1).isoformat()
    has_december_demand = any(
        row["process_id"] == process["id"]
        and row["minimum_staff"] > 0
        and row["effective_from"] <= december_start
        and (row["effective_to"] is None or row["effective_to"] >= december_start)
        for row in requirements
    )
    if not has_december_demand:
        require(client.post(requirements_url, json={
            "brand_id": process["brand_id"], "process_id": process["id"],
            "minimum_staff": 1, "effective_from": "2026-01-01",
        }), 201)


def main() -> None:
    with SessionLocal() as session:
        organization = session.scalar(select(Organization).where(Organization.name == "DataStraw Operations"))
        if organization is None:
            raise RuntimeError("Create the DataStraw Operations organization before running the demo seeder.")
        org_id = organization.id

    with authenticated_test_client() as client:
        pods = require(client.get(f"/organizations/{org_id}/pods"), 200)
        pod_by_name = {row["name"]: row for row in pods}
        for pod_name in ("Customer Care", "Digital Support", "Back Office"):
            if pod_name not in pod_by_name:
                pod_by_name[pod_name] = require(client.post(
                    f"/organizations/{org_id}/pods", json={"name": pod_name}), 201)

        brand_processes = {}
        for pod_name, brands in {
            "Customer Care": {"Northstar Retail": ["Orders", "Returns"], "Cedar Travel": ["Bookings", "Changes"]},
            "Digital Support": {"Northstar Digital": ["Chat Support", "Email Support"], "Cedar Travel Online": ["Web Support", "Account Help"]},
            "Back Office": {"Northstar Operations": ["Payment Review", "Fulfillment"], "Cedar Operations": ["Claims", "Reconciliation"]},
        }.items():
            pod_id = pod_by_name[pod_name]["id"]
            existing_brands = require(client.get(f"/organizations/{org_id}/brands"), 200)
            brand_by_name = {row["name"]: row for row in existing_brands}
            for brand_name, processes in brands.items():
                brand = brand_by_name.get(brand_name)
                if brand is None:
                    brand = require(client.post(f"/organizations/{org_id}/brands", json={
                        "pod_id": pod_id, "name": brand_name}), 201)
                elif brand["pod_id"] != pod_id:
                    raise RuntimeError(f"Brand {brand_name!r} already exists under another Pod.")
                existing_processes = require(client.get(f"/organizations/{org_id}/processes"), 200)
                process_by_name = {row["name"]: row for row in existing_processes}
                brand_processes[(pod_name, brand_name)] = []
                for process_name in processes:
                    process = process_by_name.get(process_name)
                    if process is None:
                        process = require(client.post(f"/organizations/{org_id}/processes", json={
                            "brand_id": brand["id"], "name": process_name}), 201)
                    elif process["brand_id"] != brand["id"] or process["pod_id"] != pod_id:
                        raise RuntimeError(f"Process {process_name!r} already exists under another Brand or Pod.")
                    brand_processes[(pod_name, brand_name)].append(process)
                    ensure_default_calendar(client, org_id, process)
                    requirements = require(client.get(
                        f"/organizations/{org_id}/staffing-requirements",
                        params={"process_id": process["id"]}), 200)
                    if not any(row["effective_from"] == "2026-01-01" for row in requirements):
                        require(client.post(f"/organizations/{org_id}/staffing-requirements", json={
                            "brand_id": brand["id"], "process_id": process["id"],
                            "minimum_staff": 1, "effective_from": "2026-01-01",
                        }), 201)

        current_employees = require(client.get(f"/organizations/{org_id}/employees"), 200)
        employee_by_email = {row["email"]: row for row in current_employees}
        names = [
            "Aarav Mehta", "Ananya Iyer", "Arjun Nair", "Diya Kapoor", "Ishaan Rao",
            "Kavya Menon", "Kabir Shah", "Meera Joshi", "Neha Verma", "Rohan Das",
            "Sana Khan", "Vihaan Patel", "Aditi Bose", "Dev Malhotra", "Ira Kulkarni",
            "Karan Sethi", "Mira Reddy", "Nikhil Jain", "Pooja Sen", "Reyansh Gupta",
            "Sia Thomas", "Tara Bhat", "Uday Kumar", "Vanya Roy", "Yash Arora",
            "Zoya Fernandes", "Advik Chawla", "Bhavna Pillai", "Chirag Shetty", "Esha Dutta",
        ]
        pods_for_team = ["Customer Care", "Digital Support", "Back Office"]
        created = 0
        for index, full_name in enumerate(names):
            email = f"demo.employee{index + 1:02d}@datastraw.test"
            employee = employee_by_email.get(email)
            pod_name = pods_for_team[index // 10]
            if employee is None:
                employee = require(client.post(f"/organizations/{org_id}/employees", json={
                    "pod_id": pod_by_name[pod_name]["id"], "full_name": full_name,
                    "email": email, "role": "Associate",
                }), 201)
                created += 1
            assignments = require(client.get(
                f"/organizations/{org_id}/employees/{employee['id']}/process-assignments"), 200)
            # Cross-train each employee on the team's four demo processes so
            # pod-wide generation can satisfy each Brand and Process minimum.
            team_processes = [process for (team, _), rows in brand_processes.items()
                              if team == pod_name for process in rows]
            for process in team_processes:
                assignments = ensure_process_eligibility(
                    client, org_id, employee["id"], process["id"],
                    "2026-01-01", "2026-12-31", assignments,
                )
            if index % 5 == 0:
                existing_availability = require(client.get(
                    f"/organizations/{org_id}/employees/{employee['id']}/availability",
                    params={"start_date": "2026-01-01", "end_date": "2026-12-31"}), 200)
                if not any(row["day_of_week"] == 6 and not row["is_available"] for row in existing_availability):
                    require(client.post(
                        f"/organizations/{org_id}/employees/{employee['id']}/availability",
                        json={"day_of_week": 6, "sequence": 1, "is_available": False,
                              "effective_from": "2026-01-01"}), 201)

        all_employees = require(client.get(f"/organizations/{org_id}/employees"), 200)
        all_pods = require(client.get(f"/organizations/{org_id}/pods"), 200)
        for pod in all_pods:
            if pod.get("lead_employee_id") is None:
                lead = next((row for row in all_employees
                             if row["pod_id"] == pod["id"] and row["is_active"]), None)
                if lead is None:
                    raise RuntimeError(f"No active employee is available to lead Pod {pod['name']!r}.")
                require(client.put(
                    f"/organizations/{org_id}/pods/{pod['id']}/lead",
                    json={"employee_id": lead["id"]},
                ), 200)

        # The original manually created Pod is part of the live demo scope too.
        # It previously had Processes but no operating rules, so December was
        # correctly resolved as closed. Fill only empty calendars; retain edits.
        original_pod = next((row for row in pods if row["name"] == "Customer Support"), None)
        if original_pod is not None:
            original_processes = require(client.get(f"/organizations/{org_id}/processes"), 200)
            for process in original_processes:
                if process["pod_id"] == original_pod["id"]:
                    ensure_default_calendar(client, org_id, process)
                    if process["name"] == "CS":
                        ensure_original_pod_demo_demand(client, org_id, original_pod, process)

        existing_holidays = require(client.get(
            f"/organizations/{org_id}/holidays",
            params={"start_date": "2026-12-01", "end_date": "2026-12-31"}), 200)
        if not any(row["holiday_date"] == "2026-12-25" for row in existing_holidays):
            require(client.post(f"/organizations/{org_id}/holidays", json={
                "holiday_date": "2026-12-25", "name": "Christmas Day", "region": "India"}), 201)

    with SessionLocal() as session:
        employee_count = session.scalar(select(func.count(Employee.id)).where(Employee.organization_id == org_id))
    print(f"Demo data ready for organization {org_id}: added {created} employees; "
          f"organization now has {employee_count} employees, 3 demo Pods, 6 demo Brands, "
          f"12 demo Processes, recurring calendars for previously unconfigured Processes, "
          f"staffing requirements, weekly availability rules, "
          f"and a 2026-12-25 holiday. Existing records were retained.")


if __name__ == "__main__":
    main()
