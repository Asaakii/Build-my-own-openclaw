import json
from contextlib import contextmanager
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from config import GatewayConfig
from gateway_agent_runtime import GatewayAgentResult
from gateway_server import create_gateway_server


TEST_TOKEN = "test-token-with-at-least-thirty-two-characters"


class FakeGatewayAgentRuntime:
    """只记录调用参数，不调用真实模型或工具。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def handle_text(
        self,
        session_id: str,
        text: str,
    ) -> GatewayAgentResult:
        self.calls.append((session_id, text))
        return GatewayAgentResult(
            reply=f"已收到：{text}",
            compressed_message_count=0,
        )


@contextmanager
def running_gateway(runtime: FakeGatewayAgentRuntime):
    """启动临时 Gateway，测试结束后确保停止。"""
    server = create_gateway_server(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            token=TEST_TOKEN,
        ),
        runtime,
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
    payload: object | None,
    token: str | None = TEST_TOKEN,
    content_type: str = "application/json",
) -> tuple[int, dict[str, str]]:
    """发送消息请求，并统一读取成功或错误 JSON。"""
    headers = {
        "Content-Type": content_type,
    }

    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    data = (
        json.dumps(payload).encode("utf-8")
        if payload is not None
        else None
    )
    request = Request(
        url,
        data=data,
        headers=headers,
        method="POST",
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


def test_message_api_calls_only_gateway_runtime() -> None:
    """合法请求应把指定会话和文本交给 Gateway 专用运行器。"""
    runtime = FakeGatewayAgentRuntime()

    with running_gateway(runtime) as base_url:
        status_code, payload = request_json(
            f"{base_url}/sessions/local:test/messages",
            {"text": "  测试消息  "},
        )

    assert status_code == 200
    assert payload["reply"] == "已收到：测试消息"
    assert runtime.calls == [("local:test", "测试消息")]


def test_unauthorized_message_never_reaches_runtime() -> None:
    """缺少 Token 时，消息不能进入 Agent 运行器。"""
    runtime = FakeGatewayAgentRuntime()

    with running_gateway(runtime) as base_url:
        status_code, payload = request_json(
            f"{base_url}/sessions/local:test/messages",
            {"text": "不应处理"},
            token=None,
        )

    assert status_code == 401
    assert payload == {"error": "unauthorized"}
    assert runtime.calls == []


def test_invalid_message_payload_is_rejected() -> None:
    """空消息、额外字段和超长消息都不得进入运行器。"""
    runtime = FakeGatewayAgentRuntime()

    with running_gateway(runtime) as base_url:
        url = f"{base_url}/sessions/local:test/messages"

        status_code, payload = request_json(
            url,
            {"text": ""},
        )
        assert status_code == 400
        assert payload == {"error": "message_empty"}

        status_code, payload = request_json(
            url,
            {"text": "正常", "extra": "不允许"},
        )
        assert status_code == 400
        assert payload == {
            "error": "invalid_message_payload"
        }

        status_code, payload = request_json(
            url,
            {"text": "x" * 2_001},
        )
        assert status_code == 413
        assert payload == {"error": "message_too_long"}

    assert runtime.calls == []


def test_invalid_path_and_content_type_are_rejected() -> None:
    """未知路径和非 JSON 请求不应触发运行器。"""
    runtime = FakeGatewayAgentRuntime()

    with running_gateway(runtime) as base_url:
        status_code, payload = request_json(
            f"{base_url}/sessions/../messages",
            {"text": "测试"},
        )
        assert status_code == 404
        assert payload == {"error": "not_found"}

        status_code, payload = request_json(
            f"{base_url}/sessions/local:test/messages",
            {"text": "测试"},
            content_type="text/plain",
        )
        assert status_code == 415
        assert payload == {
            "error": "unsupported_media_type"
        }

    assert runtime.calls == []