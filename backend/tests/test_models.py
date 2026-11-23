from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, configure_mappers
from sqlalchemy.pool import StaticPool

from app import models
from app.db.base import Base
from app.models import (
    Assignment,
    Employee,
    Organization,
    Pod,
    Process,
    ProcessAssignment,
    Roster,
    RosterEntry,
    RosterWorkAssignment,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
        session.rollback()
    Base.metadata.drop_all(engine)
    engine.dispose()


def make_roster_graph(session: Session):
    organization = Organization(name="Northwind")
    session.add(organization)
    session.flush()

    pod = Pod(organization_id=organization.id, name="Central")
    session.add(pod)
    session.flush()

    employee = Employee(
        organization_id=organization.id,
        pod_id=pod.id,
        full_name="Avery Employee",
        email="avery@example.test",
    )
    brand_a = models.Brand(organization_id=organization.id, pod_id=pod.id, name="Brand A")
    brand_b = models.Brand(organization_id=organization.id, pod_id=pod.id, name="Brand B")
    session.add_all([employee, brand_a, brand_b])
    session.flush()

    process_a = Process(
        organization_id=organization.id, pod_id=pod.id, brand_id=brand_a.id,
        name="Sales", timezone="Asia/Kolkata",
    )
    process_b = Process(
        organization_id=organization.id, pod_id=pod.id, brand_id=brand_b.id,
        name="Support", timezone="Asia/Kolkata",
    )
    session.add_all([process_a, process_b])
    session.flush()

    assignment_a = ProcessAssignment(
        organization_id=organization.id, pod_id=pod.id, employee_id=employee.id,
        process_id=process_a.id, effective_from=date(2026, 1, 1),
    )
    assignment_b = ProcessAssignment(
        organization_id=organization.id, pod_id=pod.id, employee_id=employee.id,
        process_id=process_b.id, effective_from=date(2026, 1, 1),
    )
    session.add_all([assignment_a, assignment_b])
    session.flush()

    roster = Roster(
        organization_id=organization.id, pod_id=pod.id,
        period_start=date(2026, 1, 5), period_end=date(2026, 1, 11), version=1,
    )
    session.add(roster)
    session.flush()
    entry = RosterEntry(
        organization_id=organization.id, pod_id=pod.id, roster_id=roster.id,
        employee_id=employee.id, work_date=date(2026, 1, 5), status="working",
    )
    session.add(entry)
    session.flush()
    return organization, pod, employee, process_a, process_b, assignment_a, assignment_b, roster, entry


def test_all_required_domain_models_are_registered() -> None:
    configure_mappers()
    required = {
        "pods", "employees", "brands", "processes", "process_assignments", "operating_rules",
        "day_overrides", "date_overrides", "holidays", "holiday_overrides", "employee_leaves",
        "employee_comp_offs", "employee_preferences", "rosters", "roster_entries",
        "roster_work_assignments", "generation_runs", "audit_logs",
    }

    assert required <= set(Base.metadata.tables)
    assert {"organizations", "shifts", "assignments", "employee_availability"} <= set(Base.metadata.tables)


def test_tenant_relationships_are_mapped() -> None:
    configure_mappers()

    assert Pod.organization.property.back_populates == "pods"
    assert Employee.pod.property.back_populates == "employees"
    assert models.Brand.pod.property.back_populates == "brands"
    assert Process.brand.property.back_populates == "processes"
    assert ProcessAssignment.employee.property.back_populates == "process_assignments"
    assert Roster.entries.property.back_populates == "roster"
    assert RosterEntry.work_assignments.property.back_populates == "roster_entry"


def test_employee_can_be_assigned_to_multiple_brands_and_processes(db_session: Session) -> None:
    graph = make_roster_graph(db_session)
    employee, assignment_a, assignment_b = graph[2], graph[5], graph[6]

    assert len(employee.process_assignments) == 2
    assert {item.process.brand.name for item in employee.process_assignments} == {"Brand A", "Brand B"}
    assert assignment_a.process_id != assignment_b.process_id


def test_one_daily_entry_can_have_multiple_work_assignments(db_session: Session) -> None:
    graph = make_roster_graph(db_session)
    organization, pod, employee, _, _, assignment_a, assignment_b, _, entry = graph
    db_session.add_all([
        RosterWorkAssignment(
            organization_id=organization.id, pod_id=pod.id, roster_entry_id=entry.id,
            employee_id=employee.id, process_assignment_id=assignment_a.id,
            starts_at=datetime(2026, 1, 5, 9, tzinfo=timezone.utc),
            ends_at=datetime(2026, 1, 5, 13, tzinfo=timezone.utc),
        ),
        RosterWorkAssignment(
            organization_id=organization.id, pod_id=pod.id, roster_entry_id=entry.id,
            employee_id=employee.id, process_assignment_id=assignment_b.id,
            starts_at=datetime(2026, 1, 5, 14, tzinfo=timezone.utc),
            ends_at=datetime(2026, 1, 5, 18, tzinfo=timezone.utc),
        ),
    ])
    db_session.commit()

    assert len(entry.work_assignments) == 2


def test_roster_entry_enforces_one_daily_status_per_roster(db_session: Session) -> None:
    graph = make_roster_graph(db_session)
    organization, pod, employee, _, _, _, _, roster, _ = graph
    db_session.add(RosterEntry(
        organization_id=organization.id, pod_id=pod.id, roster_id=roster.id,
        employee_id=employee.id, work_date=date(2026, 1, 5), status="day_off",
    ))

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_process_assignment_cannot_cross_pods(db_session: Session) -> None:
    graph = make_roster_graph(db_session)
    organization, employee, process_a = graph[0], graph[2], graph[3]
    other_pod = Pod(organization_id=organization.id, name="West")
    db_session.add(other_pod)
    db_session.flush()
    db_session.add(ProcessAssignment(
        organization_id=organization.id, pod_id=other_pod.id, employee_id=employee.id,
        process_id=process_a.id, effective_from=date(2026, 1, 1),
    ))

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_sqlite_foreign_keys_are_enabled(db_session: Session) -> None:
    assert db_session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    db_session.add(Assignment(organization_id=1, employee_id=900, shift_id=900))

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_effective_date_check_constraint(db_session: Session) -> None:
    graph = make_roster_graph(db_session)
    organization, pod, employee, process = graph[0], graph[1], graph[2], graph[3]
    db_session.add(ProcessAssignment(
        organization_id=organization.id, pod_id=pod.id, employee_id=employee.id,
        process_id=process.id, effective_from=date(2026, 2, 2), effective_to=date(2026, 2, 1),
    ))

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_employee_email_is_unique_within_organization(db_session: Session) -> None:
    organization, pod, employee, *_ = make_roster_graph(db_session)
    db_session.add(Employee(
        organization_id=organization.id, pod_id=pod.id,
        full_name="Duplicate Email", email=employee.email,
    ))

    with pytest.raises(IntegrityError):
        db_session.flush()
