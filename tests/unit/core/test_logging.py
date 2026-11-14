import json
import logging
import sys

from app.core.logging import JsonFormatter, request_id_var


def make_record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.meal_planning", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_log_line_is_json_with_level_logger_message_and_utc_timestamp() -> None:
    entry = json.loads(JsonFormatter().format(make_record("Meal plan generated")))

    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.meal_planning"
    assert entry["message"] == "Meal plan generated"
    assert entry["timestamp"].endswith("+00:00")


def test_extra_fields_become_top_level_keys() -> None:
    record = make_record("Meal plan generated", meal_plan_id="4f1c", days=7)

    entry = json.loads(JsonFormatter().format(record))

    assert entry["meal_plan_id"] == "4f1c"
    assert entry["days"] == 7


def test_current_request_id_is_attached() -> None:
    token = request_id_var.set("req-dxb-42")
    try:
        entry = json.loads(JsonFormatter().format(make_record("hello")))
    finally:
        request_id_var.reset(token)

    assert entry["request_id"] == "req-dxb-42"


def test_no_request_id_key_outside_a_request() -> None:
    entry = json.loads(JsonFormatter().format(make_record("startup")))

    assert "request_id" not in entry


def test_exception_traceback_is_included() -> None:
    try:
        raise ValueError("bad macro total")
    except ValueError:
        record = make_record("failed")
        record.exc_info = sys.exc_info()

    entry = json.loads(JsonFormatter().format(record))

    assert "ValueError: bad macro total" in entry["exception"]
