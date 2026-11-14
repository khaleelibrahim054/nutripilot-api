import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("log_level", "VERBOSE"),
        ("ai_service_url", "not a url"),
        ("ai_service_timeout_seconds", 0),
        ("ai_service_timeout_seconds", -5),
        ("ai_service_stream_idle_timeout_seconds", 10_000),
        ("db_pool_size", 0),
    ],
)
def test_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings(**{field: value})


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AI_SERVICE_URL", "http://ai-service.internal:8100")

    settings = Settings()

    assert settings.log_level == "DEBUG"
    assert str(settings.ai_service_url) == "http://ai-service.internal:8100/"
