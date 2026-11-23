from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import ProcessAssignmentRead


def test_effective_window_schema_accepts_open_ended_assignment() -> None:
    result = ProcessAssignmentRead(
        id=1,
        organization_id=1,
        pod_id=2,
        employee_id=3,
        process_id=4,
        effective_from=date(2026, 1, 1),
        effective_to=None,
    )

    assert result.effective_to is None


def test_effective_window_schema_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        ProcessAssignmentRead(
            id=1,
            organization_id=1,
            pod_id=2,
            employee_id=3,
            process_id=4,
            effective_from=date(2026, 2, 2),
            effective_to=date(2026, 2, 1),
        )
