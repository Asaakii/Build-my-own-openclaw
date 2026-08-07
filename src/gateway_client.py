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


@dataclass(frozen=True)
class GatewayMessageResult:
    """保存 Gateway 返回的一轮正式回答。"""

    reply: str
    compressed_message_count: int


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
                "消息超过 Gateway 允许范围。"
            ) from error

        if 400 <= error.code < 500:
            raise GatewayClientError(
                "Gateway 拒绝了消息请求。"
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
    )

    if (
        not isinstance(payload, dict)
        or any(
            not isinstance(payload.get(field), str)
            for field in required_fields
        )
    ):
        raise GatewayClientError(
            "Gateway 返回了无效状态数据。"
        )

    return GatewayStatus(
        status=payload["status"],
        version=payload["version"],
        started_at=payload["started_at"],
        address=payload["address"],
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