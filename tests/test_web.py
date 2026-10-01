"""Unit tests for web.py's describe_pending_activities() -- the pure mapping from raw
PendingActivityInfo protos to the JSON shape the GUI renders as a live "retrying" step. No real
Temporal connection; fakes just enough of the proto's shape to exercise the mapping.
"""

from web import describe_pending_activities


class FakeFailure:
    def __init__(self, message: str) -> None:
        self.message = message


class FakeActivityType:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeTimestamp:
    def __init__(self, iso: str) -> None:
        self._iso = iso

    def ToJsonString(self) -> str:
        return self._iso


class FakePendingActivityInfo:
    def __init__(
        self,
        *,
        activity_type: str,
        attempt: int,
        last_failure: FakeFailure | None = None,
        next_attempt_schedule_time: FakeTimestamp | None = None,
    ) -> None:
        self.activity_type = FakeActivityType(activity_type)
        self.attempt = attempt
        self.last_failure = last_failure
        self.next_attempt_schedule_time = next_attempt_schedule_time

    def HasField(self, name: str) -> bool:  # noqa: N802 -- matches the real protobuf API
        return getattr(self, name) is not None


def test_describe_pending_activities_empty_list_in_empty_list_out():
    assert describe_pending_activities([]) == []


def test_describe_pending_activities_filters_out_first_attempts():
    """A healthy first attempt in flight isn't a retry -- only attempt > 1 should surface."""
    healthy = FakePendingActivityInfo(activity_type="invoke_model", attempt=1)
    assert describe_pending_activities([healthy]) == []


def test_describe_pending_activities_maps_a_retrying_activity():
    retrying = FakePendingActivityInfo(
        activity_type="invoke_model",
        attempt=2,
        last_failure=FakeFailure("connection refused"),
        next_attempt_schedule_time=FakeTimestamp("2026-01-01T00:00:05Z"),
    )

    assert describe_pending_activities([retrying]) == [
        {
            "activity_type": "invoke_model",
            "attempt": 2,
            "last_failure": "connection refused",
            "next_attempt_schedule_time": "2026-01-01T00:00:05Z",
        }
    ]


def test_describe_pending_activities_handles_missing_optional_fields():
    """last_failure/next_attempt_schedule_time are only set once a failure has actually
    happened/a retry is scheduled -- HasField must gate reading them, not a truthiness check,
    since an unset proto field still returns a (falsy-looking) default object otherwise."""
    retrying_no_details = FakePendingActivityInfo(activity_type="get_recent_aws_announcements", attempt=3)

    assert describe_pending_activities([retrying_no_details]) == [
        {
            "activity_type": "get_recent_aws_announcements",
            "attempt": 3,
            "last_failure": None,
            "next_attempt_schedule_time": None,
        }
    ]


def test_describe_pending_activities_mixes_retrying_and_healthy_activities():
    healthy = FakePendingActivityInfo(activity_type="get_recent_aws_announcements", attempt=1)
    retrying = FakePendingActivityInfo(activity_type="invoke_model", attempt=2, last_failure=FakeFailure("timeout"))

    result = describe_pending_activities([healthy, retrying])

    assert len(result) == 1
    assert result[0]["activity_type"] == "invoke_model"
