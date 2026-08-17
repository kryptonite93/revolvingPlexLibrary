from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.presentation import format_local_date, format_local_datetime
from app.settings import Settings


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 8, 17, 5, 30, tzinfo=UTC),
        datetime(2026, 8, 17, 5, 30),
    ],
)
def test_local_timestamp_uses_configured_timezone(value: datetime) -> None:
    assert format_local_datetime(value, "America/Toronto") == "2026-08-17 01:30 EDT"
    assert format_local_date(value, "America/Toronto") == "2026-08-17"


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown IANA timezone"):
        Settings(timezone="Not/A_Timezone")
