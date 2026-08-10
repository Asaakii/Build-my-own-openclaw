import json
from contextlib import contextmanager
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from config import GatewayConfig
from gateway_server import create_gateway_server


TEST_TOKEN = "test-token-with-at-least-thirty-two-characters"


@contextmanager
def running_gateway():
    """启动临时本机服务，测试结束后确保停止。"""
    server = create_gateway_server(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            token=TEST_TOKEN,
        )
    )
    thread = Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def request_json(
    url: str,
    method: str = "GET",
    token: str | None = TEST_TOKEN,
) -> tuple[int, dict[str, str]]:
    """发送测试请求并统一读取成功或错误 JSON。"""
    headers: dict[str, str] = {}

    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(
        url,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=2) as response:
            return (
                response.status,
                json.loads(response.read()),
            )
    except HTTPError as error:
        return (
            error.code,
            json.loads(error.read()),
        )


def test_health_requires_valid_token() -> None:
    """健康检查也必须先通过 Token 校验。"""
    with running_gateway() as base_url:
        status_code, payload = request_json(
            f"{base_url}/health",
        )
        assert status_code == 200
        assert payload == {"status": "ok"}

        status_code, payload = request_json(
            f"{base_url}/health",
            token=None,
        )
        assert status_code == 401
        assert payload == {"error": "unauthorized"}

        status_code, payload = request_json(
            f"{base_url}/health",
            token="wrong-token",
        )
        assert status_code == 401
        assert payload == {"error": "unauthorized"}


def test_status_returns_no_token() -> None:
    """状态接口只返回运行信息，不能回显认证 Token。"""
    with running_gateway() as base_url:
        status_code, payload = request_json(
            f"{base_url}/status",
        )

        assert status_code == 200
        assert payload["status"] == "running"
        assert payload["address"].startswith("127.0.0.1:")
        assert payload["diagnostics"] == {
            "agent_runtime": "unavailable",
            "state_store": "unavailable",
            "reminder_service": "unavailable",
            "tool_policy": "restricted",
        }
        assert TEST_TOKEN not in json.dumps(payload)


def test_unknown_path_and_write_request_are_rejected() -> None:
    """未知路径和尚未实现的写请求必须明确拒绝。"""
    with running_gateway() as base_url:
        status_code, payload = request_json(
            f"{base_url}/unknown",
        )
        assert status_code == 404
        assert payload == {"error": "not_found"}

        status_code, payload = request_json(
            f"{base_url}/health",
            method="POST",
        )
        assert status_code == 405
        assert payload == {"error": "method_not_allowed"}
