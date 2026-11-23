from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Brand, Organization, Pod, Process


class ResourceNotFound(Exception):
    pass


class ResourceConflict(Exception):
    pass


class DuplicateResource(ResourceConflict):
    pass


def _is_unique_violation(error: IntegrityError) -> bool:
    """Recognize unique violations across SQLite and PostgreSQL drivers."""
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate == "23505":
        return True
    diagnostic = getattr(original, "diag", None)
    if getattr(diagnostic, "constraint_name", None):
        # PostgreSQL reports the violated constraint name for all constraint
        # failures; only the unique-violation SQLSTATE above identifies this
        # as a duplicate.
        return sqlstate == "23505"
    message = str(original).lower()
    return "unique constraint failed" in message or "is not unique" in message


def _save(session: Session, resource, duplicate_message: str = "A record with these identifiers already exists."):
    session.add(resource)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_unique_violation(exc):
            raise DuplicateResource(duplicate_message) from exc
        raise ResourceConflict("The change conflicts with existing related data.") from exc
    session.refresh(resource)
    return resource


def _organization(session: Session, organization_id: int) -> Organization:
    organization = session.get(Organization, organization_id)
    if organization is None:
        raise ResourceNotFound("Organization not found.")
    return organization


def _pod(session: Session, organization_id: int, pod_id: int) -> Pod:
    pod = session.scalar(
        select(Pod).where(Pod.id == pod_id, Pod.organization_id == organization_id)
    )
    if pod is None:
        raise ResourceNotFound("Pod not found in this organization.")
    return pod


def _brand(session: Session, organization_id: int, brand_id: int) -> Brand:
    brand = session.scalar(
        select(Brand).where(Brand.id == brand_id, Brand.organization_id == organization_id)
    )
    if brand is None:
        raise ResourceNotFound("Brand not found in this organization.")
    return brand


def _process(session: Session, organization_id: int, process_id: int) -> Process:
    process = session.scalar(
        select(Process).where(Process.id == process_id, Process.organization_id == organization_id)
    )
    if process is None:
        raise ResourceNotFound("Process not found in this organization.")
    return process


def list_organizations(session: Session) -> list[Organization]:
    return list(session.scalars(select(Organization).order_by(Organization.id)))


def create_organization(session: Session, name: str) -> Organization:
    return _save(session, Organization(name=name))


def get_organization(session: Session, organization_id: int) -> Organization:
    return _organization(session, organization_id)


def update_organization(session: Session, organization_id: int, name: str) -> Organization:
    organization = _organization(session, organization_id)
    organization.name = name
    return _save(session, organization)


def list_pods(session: Session, organization_id: int) -> list[Pod]:
    _organization(session, organization_id)
    statement = select(Pod).where(Pod.organization_id == organization_id).order_by(Pod.id)
    return list(session.scalars(statement))


def create_pod(session: Session, organization_id: int, name: str) -> Pod:
    _organization(session, organization_id)
    duplicate = session.scalar(
        select(Pod.id).where(Pod.organization_id == organization_id, Pod.name == name)
    )
    if duplicate is not None:
        raise DuplicateResource("A pod with this name already exists in the organization.")
    return _save(session, Pod(organization_id=organization_id, name=name), "A pod with this name already exists in the organization.")


def get_pod(session: Session, organization_id: int, pod_id: int) -> Pod:
    return _pod(session, organization_id, pod_id)


def update_pod(session: Session, organization_id: int, pod_id: int, name: str) -> Pod:
    pod = _pod(session, organization_id, pod_id)
    duplicate = session.scalar(
        select(Pod.id).where(
            Pod.organization_id == organization_id, Pod.name == name, Pod.id != pod_id
        )
    )
    if duplicate is not None:
        raise DuplicateResource("A pod with this name already exists in the organization.")
    pod.name = name
    return _save(session, pod, "A pod with this name already exists in the organization.")


def list_brands(session: Session, organization_id: int) -> list[Brand]:
    _organization(session, organization_id)
    statement = select(Brand).where(Brand.organization_id == organization_id).order_by(Brand.id)
    return list(session.scalars(statement))


def create_brand(session: Session, organization_id: int, pod_id: int, name: str) -> Brand:
    _pod(session, organization_id, pod_id)
    duplicate = session.scalar(
        select(Brand.id).where(
            Brand.organization_id == organization_id, Brand.pod_id == pod_id, Brand.name == name
        )
    )
    if duplicate is not None:
        raise DuplicateResource("A brand with this name already exists in the pod.")
    brand = Brand(organization_id=organization_id, pod_id=pod_id, name=name)
    return _save(session, brand, "A brand with this name already exists in the pod.")


def get_brand(session: Session, organization_id: int, brand_id: int) -> Brand:
    return _brand(session, organization_id, brand_id)


def update_brand(
    session: Session, organization_id: int, brand_id: int, pod_id: int, name: str
) -> Brand:
    brand = _brand(session, organization_id, brand_id)
    _pod(session, organization_id, pod_id)
    duplicate = session.scalar(
        select(Brand.id).where(
            Brand.organization_id == organization_id,
            Brand.pod_id == pod_id,
            Brand.name == name,
            Brand.id != brand_id,
        )
    )
    if duplicate is not None:
        raise DuplicateResource("A brand with this name already exists in the pod.")
    brand.pod_id = pod_id
    brand.name = name
    return _save(session, brand, "A brand with this name already exists in the pod.")


def list_processes(session: Session, organization_id: int) -> list[Process]:
    _organization(session, organization_id)
    statement = select(Process).where(Process.organization_id == organization_id).order_by(Process.id)
    return list(session.scalars(statement))


def create_process(
    session: Session, organization_id: int, brand_id: int, name: str, timezone: str
) -> Process:
    brand = _brand(session, organization_id, brand_id)
    duplicate = session.scalar(
        select(Process.id).where(
            Process.organization_id == organization_id,
            Process.brand_id == brand_id,
            Process.name == name,
        )
    )
    if duplicate is not None:
        raise DuplicateResource("A process with this name already exists in the brand.")
    process = Process(
        organization_id=organization_id,
        pod_id=brand.pod_id,
        brand_id=brand.id,
        name=name,
        timezone=timezone,
    )
    return _save(session, process, "A process with this name already exists in the brand.")


def get_process(session: Session, organization_id: int, process_id: int) -> Process:
    return _process(session, organization_id, process_id)


def update_process(
    session: Session,
    organization_id: int,
    process_id: int,
    brand_id: int,
    name: str,
    timezone: str,
) -> Process:
    process = _process(session, organization_id, process_id)
    brand = _brand(session, organization_id, brand_id)
    duplicate = session.scalar(
        select(Process.id).where(
            Process.organization_id == organization_id,
            Process.brand_id == brand_id,
            Process.name == name,
            Process.id != process_id,
        )
    )
    if duplicate is not None:
        raise DuplicateResource("A process with this name already exists in the brand.")
    process.brand_id = brand.id
    process.pod_id = brand.pod_id
    process.name = name
    process.timezone = timezone
    return _save(session, process, "A process with this name already exists in the brand.")
