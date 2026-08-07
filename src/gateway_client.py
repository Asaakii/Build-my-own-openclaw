import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import GatewayConfig


GATEWAY_REQUEST_TIMEOUT_SECONDS = 3


class GatewayClientError(RuntimeError):
    """表示 CLI 无法安全完成 Gateway 请求。"""


@dataclass(frozen=True)
class GatewayStatus:
    """保存 CLI 可以安全展示的 Gateway 状态。"""

    status: str
    version: str
    started_at: str
    address: str


def get_gateway_status(
    config: GatewayConfig,
) -> GatewayStatus:
    """携带本机 Token 查询 Gateway 状态，不打印 Token。"""
    request = Request(
        f"http://{config.host}:{config.port}/status",
        headers={
            "Authorization": f"Bearer {config.token}",
        },
        method="GET",
    )

    try:
        with urlopen(
            request,
            timeout=GATEWAY_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code == 401:
            raise GatewayClientError(
                "Gateway 认证失败，请检查本机 Token。"
            ) from error

        raise GatewayClientError(
            f"Gateway 返回错误状态码: {error.code}"
        ) from error
    except URLError as error:
        raise GatewayClientError(
            "Gateway 不可用，请先运行 myclaw gateway run。"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise GatewayClientError(
            "Gateway 返回了无效状态数据。"
        ) from error

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