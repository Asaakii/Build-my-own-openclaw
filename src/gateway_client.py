import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from config import GatewayConfig


GATEWAY_REQUEST_TIMEOUT_SECONDS = 30


class GatewayClientError(RuntimeError):
    """表示 CLI 无法安全完成 Gateway 请求。"""


@dataclass(frozen=True)
class GatewayStatus:
    """保存 CLI 可以安全展示的 Gateway 状态。"""

    status: str
    version: str
    started_at: str
    address: str
    diagnostics: dict[str, str]


@dataclass(frozen=True)
class GatewayMessageResult:
    """保存 Gateway 返回的一轮正式回答。"""

    reply: str
    compressed_message_count: int


@dataclass(frozen=True)
class GatewaySessionInfo:
    """保存 CLI 可以展示的会话元数据，不含消息正文。"""

    session_id: str
    message_count: int
    has_summary: bool
    updated_at: str


@dataclass(frozen=True)
class GatewayLogResult:
    """保存 Gateway 已脱敏的运行事件。"""

    events: list[str]


@dataclass(frozen=True)
class GatewayReminderResult:
    """保存 Gateway 已创建提醒的安全元数据。"""

    task_id: str
    due_at: str
    status: str


@dataclass(frozen=True)
class GatewayTaskInfo:
    """保存任务列表元数据，不含提醒正文或投递地址。"""

    task_id: str
    session_id: str
    task_type: str
    status: str
    due_at: str
    updated_at: str


def request_gateway_json(request: Request) -> object:
    """发送本机请求并读取 JSON，不回显 Token 或请求正文。"""
    try:
        with urlopen(
            request,
            timeout=GATEWAY_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            return json.loads(response.read())
    except HTTPError as error:
        if error.code == 401:
            raise GatewayClientError(
                "Gateway 认证失败，请检查本机 Token。"
            ) from error

        if error.code == 404:
            raise GatewayClientError(
                "Gateway 拒绝了会话标识或接口路径。"
            ) from error

        if error.code == 413:
            raise GatewayClientError(
                "请求超过 Gateway 允许范围。"
            ) from error

        if 400 <= error.code < 500:
            raise GatewayClientError(
                "Gateway 拒绝了请求。"
            ) from error

        raise GatewayClientError(
            "Gateway 无法完成本轮请求。"
        ) from error
    except URLError as error:
        raise GatewayClientError(
            "Gateway 不可用，请先运行 myclaw gateway run。"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise GatewayClientError(
            "Gateway 返回了无效数据。"
        ) from error


def get_gateway_status(
    config: GatewayConfig,
) -> GatewayStatus:
    """携带本机 Token 查询 Gateway 状态。"""
    request = Request(
        f"http://{config.host}:{config.port}/status",
        headers={
            "Authorization": f"Bearer {config.token}",
        },
        method="GET",
    )
    payload = request_gateway_json(request)

    required_fields = (
        "status",
        "version",
        "started_at",
        "address",
        "diagnostics",
    )

    if (
        not isinstance(payload, dict)
        or any(
            (
                not isinstance(payload.get(field), str)
                if field != "diagnostics"
                else not isinstance(payload.get(field), dict)
            )
            for field in required_fields
        )
        or not all(
            isinstance(value, str)
            for value in payload["diagnostics"].values()
        )
        or set(payload["diagnostics"]) != {
            "agent_runtime",
            "state_store",
            "reminder_service",
            "tool_policy",
        }
    ):
        raise GatewayClientError(
            "Gateway 返回了无效状态数据。"
        )

    return GatewayStatus(
        status=payload["status"],
        version=payload["version"],
        started_at=payload["started_at"],
        address=payload["address"],
        diagnostics=payload["diagnostics"],
    )


def send_gateway_message(
    config: GatewayConfig,
    session_id: str,
    text: str,
) -> GatewayMessageResult:
    """向指定会话发送文字消息，由 Gateway 独占处理。"""
    # 只保留会话标识协议允许的字符；非法字符会编码后被服务端拒绝。
    encoded_session_id = quote(
        session_id,
        safe=":_-",
    )
    body = json.dumps(
        {"text": text},
        ensure_ascii=False,
    ).encode("utf-8")

    request = Request(
        (
            f"http://{config.host}:{config.port}"
            f"/sessions/{encoded_session_id}/messages"
        ),
        data=body,
        headers={
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    payload = request_gateway_json(request)

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("reply"), str)
        or not isinstance(
            payload.get("compressed_message_count"),
            str,
        )
    ):
        raise GatewayClientError(
            "Gateway 返回了无效消息数据。"
        )

    try:
        compressed_message_count = int(
            payload["compressed_message_count"]
        )
    except ValueError as error:
        raise GatewayClientError(
            "Gateway 返回了无效消息数据。"
        ) from error

    if compressed_message_count < 0:
        raise GatewayClientError(
            "Gateway 返回了无效消息数据。"
        )

    return GatewayMessageResult(
        reply=payload["reply"],
        compressed_message_count=compressed_message_count,
    )


def create_gateway_reminder(
    config: GatewayConfig,
    session_id: str,
    delay_seconds: int,
    content: str,
    delivery: dict[str, str],
) -> GatewayReminderResult:
    """只经 Gateway 创建提醒，客户端不创建本地定时器。"""
    body = json.dumps(
        {
            "session_id": session_id,
            "delay_seconds": delay_seconds,
            "content": content,
            "delivery": delivery,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = Request(
        f"http://{config.host}:{config.port}/tasks/reminders",
        data=body,
        headers={
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    payload = request_gateway_json(request)

    required_fields = ("task_id", "due_at", "status")

    if (
        not isinstance(payload, dict)
        or any(
            not isinstance(payload.get(field), str)
            for field in required_fields
        )
        or payload["status"] != "pending"
    ):
        raise GatewayClientError(
            "Gateway 返回了无效提醒数据。"
        )

    return GatewayReminderResult(
        task_id=payload["task_id"],
        due_at=payload["due_at"],
        status=payload["status"],
    )


def list_gateway_sessions(
    config: GatewayConfig,
) -> list[GatewaySessionInfo]:
    """通过 Gateway 读取会话元数据，不直接访问 SQLite。"""
    request = Request(
        f"http://{config.host}:{config.port}/sessions",
        headers={
            "Authorization": f"Bearer {config.token}",
        },
        method="GET",
    )
    payload = request_gateway_json(request)

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("sessions"), list)
    ):
        raise GatewayClientError(
            "Gateway 返回了无效会话数据。"
        )

    sessions: list[GatewaySessionInfo] = []

    for item in payload["sessions"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("session_id"), str)
            or not isinstance(item.get("message_count"), int)
            or isinstance(item.get("message_count"), bool)
            or not isinstance(item.get("has_summary"), bool)
            or not isinstance(item.get("updated_at"), str)
            or item["message_count"] < 0
        ):
            raise GatewayClientError(
                "Gateway 返回了无效会话数据。"
            )

        sessions.append(
            GatewaySessionInfo(
                session_id=item["session_id"],
                message_count=item["message_count"],
                has_summary=item["has_summary"],
                updated_at=item["updated_at"],
            )
        )

    return sessions


def get_gateway_logs(
    config: GatewayConfig,
    limit: int,
) -> GatewayLogResult:
    """通过 Gateway 读取数量受限的脱敏日志事件。"""
    request = Request(
        (
            f"http://{config.host}:{config.port}"
            f"/logs?limit={limit}"
        ),
        headers={
            "Authorization": f"Bearer {config.token}",
        },
        method="GET",
    )
    payload = request_gateway_json(request)

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("events"), list)
        or not all(
            isinstance(event, str)
            for event in payload["events"]
        )
    ):
        raise GatewayClientError(
            "Gateway 返回了无效日志数据。"
        )

    return GatewayLogResult(events=payload["events"])


def list_gateway_tasks(
    config: GatewayConfig,
) -> list[GatewayTaskInfo]:
    """通过 Gateway 读取任务元数据，不直接访问 SQLite。"""
    request = Request(
        f"http://{config.host}:{config.port}/tasks",
        headers={
            "Authorization": f"Bearer {config.token}",
        },
        method="GET",
    )
    payload = request_gateway_json(request)

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("tasks"), list)
    ):
        raise GatewayClientError(
            "Gateway 返回了无效任务数据。"
        )

    tasks: list[GatewayTaskInfo] = []
    required_fields = (
        "task_id",
        "session_id",
        "task_type",
        "status",
        "due_at",
        "updated_at",
    )

    for item in payload["tasks"]:
        if (
            not isinstance(item, dict)
            or any(
                not isinstance(item.get(field), str)
                for field in required_fields
            )
        ):
            raise GatewayClientError(
                "Gateway 返回了无效任务数据。"
            )

        tasks.append(
            GatewayTaskInfo(
                task_id=item["task_id"],
                session_id=item["session_id"],
                task_type=item["task_type"],
                status=item["status"],
                due_at=item["due_at"],
                updated_at=item["updated_at"],
            )
        )

    return tasks
