import json
from contextlib import contextmanager
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from config import GatewayConfig
from persistent_reminder_service import ReminderTaskResult
from sqlite_state_store import TaskInfo
from gateway_server import create_gateway_server


TEST_TOKEN = "test-token-with-at-least-thirty-two-characters"


class FakeReminderService:
    """记录接口参数，但不运行真实网络投递。"""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.tasks = [
            TaskInfo(
                task_id="task-test",
                session_id="telegram:123",
                task_type="reminder",
                status="pending",
                due_at="2026-03-07T00:00:10+00:00",
                updated_at="2026-03-07T00:00:00+00:00",
            )
        ]

    def create_reminder(self, **kwargs: object) -> ReminderTaskResult:
        self.create_calls.append(kwargs)
        return ReminderTaskResult(
            task_id="task-test",
            due_at="2026-03-07T00:00:10+00:00",
            status="pending",
        )

    def list_tasks(self) -> list[TaskInfo]:
        return self.tasks


@contextmanager
def running_gateway(service: FakeReminderService):
    """启动临时 Gateway，测试后确保关闭网络线程。"""
    server = create_gateway_server(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            token=TEST_TOKEN,
        ),
        reminder_service=service,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def request_json(
    url: str,
    method: str,
    payload: object | None = None,
    token: str | None = TEST_TOKEN,
) -> tuple[int, dict[str, object]]:
    """发送 API 请求并读取成功或错误 JSON。"""
    headers = {"Content-Type": "application/json"}

    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(
        url,
        data=(
            json.dumps(payload).encode("utf-8")
            if payload is not None
            else None
        ),
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def valid_payload() -> dict[str, object]:
    """构造符合 Gateway 边界的最小提醒任务。"""
    return {
        "session_id": "telegram:123",
        "delay_seconds": 10,
        "content": "提醒验证",
        "delivery": {
            "channel": "telegram",
            "conversation_id": "123",
        },
    }


def test_reminder_api_creates_task_only_through_service() -> None:
    """合法请求必须被转交给 Gateway 的提醒服务。"""
    service = FakeReminderService()

    with running_gateway(service) as base_url:
        status_code, payload = request_json(
            f"{base_url}/tasks/reminders",
            "POST",
            valid_payload(),
        )

    assert status_code == 201
    assert payload == {
        "task_id": "task-test",
        "due_at": "2026-03-07T00:00:10+00:00",
        "status": "pending",
    }
    assert service.create_calls == [valid_payload()]


def test_reminder_api_rejects_unauthorized_and_invalid_payload() -> None:
    """认证和严格字段校验必须在调用服务前完成。"""
    service = FakeReminderService()

    with running_gateway(service) as base_url:
        status_code, payload = request_json(
            f"{base_url}/tasks/reminders",
            "POST",
            valid_payload(),
            token=None,
        )
        assert status_code == 401
        assert payload == {"error": "unauthorized"}

        invalid_payload = valid_payload()
        invalid_payload["extra"] = "not-allowed"
        status_code, payload = request_json(
            f"{base_url}/tasks/reminders",
            "POST",
            invalid_payload,
        )
        assert status_code == 400
        assert payload == {"error": "invalid_reminder_payload"}

    assert service.create_calls == []


def test_task_list_returns_metadata_without_content_or_delivery() -> None:
    """任务列表可运维但不泄露提醒正文和 Telegram 投递地址。"""
    service = FakeReminderService()

    with running_gateway(service) as base_url:
        status_code, payload = request_json(
            f"{base_url}/tasks",
            "GET",
        )

    assert status_code == 200
    assert payload["tasks"] == [
        {
            "task_id": "task-test",
            "session_id": "telegram:123",
            "task_type": "reminder",
            "status": "pending",
            "due_at": "2026-03-07T00:00:10+00:00",
            "updated_at": "2026-03-07T00:00:00+00:00",
        }
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "提醒验证" not in serialized
    assert "conversation_id" not in serialized
