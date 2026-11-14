import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("environment", "expected"),
    [("local", True), ("test", True), ("staging", True), ("production", False)],
)
def test_api_docs_are_disabled_only_in_production(environment: str, expected: bool) -> None:
    assert Settings(environment=environment).docs_enabled is expected


def test_rejects_unknown_environment() -> None:
    with pytest.raises(ValueError, match="environment"):
        Settings(environment="prod-ish")
