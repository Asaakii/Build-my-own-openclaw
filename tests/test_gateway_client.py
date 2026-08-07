import json
from contextlib import contextmanager
from threading import Thread
from urllib.error import URLError

import pytest

import gateway_client
from config import GatewayConfig
from gateway_agent_runtime import GatewayAgentResult
from gateway_client import (
    GatewayClientError,
    send_gateway_message,
)
from gateway_server import create_gateway_server


TEST_TOKEN = "test-token-with-at-least-thirty-two-characters"


class FakeGatewayAgentRuntime:
    """提供确定性回答，不调用真实模型。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def handle_text(
        self,
        session_id: str,
        text: str,
    ) -> GatewayAgentResult:
        self.calls.append((session_id, text))
        return GatewayAgentResult(
            reply="测试回答",
            compressed_message_count=2,
        )


@contextmanager
def running_gateway(runtime: FakeGatewayAgentRuntime):
    """启动临时本机 Gateway，结束后确保关闭。"""
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
        yield GatewayConfig(
            host="127.0.0.1",
            port=server.server_port,
            token=TEST_TOKEN,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_client_sends_message_through_gateway() -> None:
    """客户端应通过 HTTP 调用 Gateway，而不是直接创建 Agent。"""
    runtime = FakeGatewayAgentRuntime()

    with running_gateway(runtime) as config:
        result = send_gateway_message(
            config,
            "local:client-test",
            "测试消息",
        )

    assert result.reply == "测试回答"
    assert result.compressed_message_count == 2
    assert runtime.calls == [
        ("local:client-test", "测试消息")
    ]


def test_client_reports_authentication_failure() -> None:
    """错误 Token 应由 Gateway 拒绝，客户端给出明确提示。"""
    runtime = FakeGatewayAgentRuntime()

    with running_gateway(runtime) as config:
        wrong_config = GatewayConfig(
            host=config.host,
            port=config.port,
            token="wrong-token-with-at-least-thirty-two-characters",
        )

        with pytest.raises(
            GatewayClientError,
            match="认证失败",
        ):
            send_gateway_message(
                wrong_config,
                "local:client-test",
                "测试消息",
            )

    assert runtime.calls == []


def test_client_reports_unavailable_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接失败时不打印底层网络细节。"""
    def raise_url_error(*_args, **_kwargs):
        raise URLError("测试网络错误")

    monkeypatch.setattr(
        gateway_client,
        "urlopen",
        raise_url_error,
    )

    with pytest.raises(
        GatewayClientError,
        match="Gateway 不可用",
    ):
        send_gateway_message(
            GatewayConfig(
                host="127.0.0.1",
                port=18790,
                token=TEST_TOKEN,
            ),
            "local:client-test",
            "测试消息",
        )