import pytest

from myclaw import cli
from config import GatewayConfig
from gateway_client import (
    GatewayClientError,
    GatewayLogResult,
    GatewayStatus,
    GatewayMessageResult,
    GatewaySessionInfo,
)


def test_config_check_prints_safe_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """配置检查应展示摘要，但不需要读取真实 .env。"""
    monkeypatch.setattr(
        cli,
        "describe_model_config",
        lambda: "模型供应商: demo\nAPI Key: 已配置（已隐藏）",
    )

    exit_code = cli.main(["config", "check"])

    assert exit_code == 0
    assert "模型供应商: demo" in capsys.readouterr().out


def test_config_check_returns_error_for_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """底层配置无效时，CLI 返回失败码而不是抛出未处理异常。"""
    def raise_config_error() -> str:
        raise ValueError("缺少必备配置: LLM_API_KEY")

    monkeypatch.setattr(
        cli,
        "describe_model_config",
        raise_config_error,
    )

    exit_code = cli.main(["config", "check"])

    assert exit_code == 1
    assert "配置检查失败" in capsys.readouterr().out


def test_unknown_command_is_rejected() -> None:
    """未知子命令必须由 argparse 以失败状态拒绝。"""
    with pytest.raises(SystemExit) as error:
        cli.main(["unknown-command"])

    assert error.value.code == 2


def test_gateway_status_uses_gateway_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """状态命令只能通过 Gateway 客户端获取信息。"""
    monkeypatch.setattr(
        cli,
        "load_gateway_config",
        lambda: GatewayConfig(
            host="127.0.0.1",
            port=18790,
            token="test-token-with-at-least-thirty-two-characters",
        ),
    )
    monkeypatch.setattr(
        cli,
        "get_gateway_status",
        lambda _config: GatewayStatus(
            status="running",
            version="0.1.0",
            started_at="2026-03-07T00:00:00+00:00",
            address="127.0.0.1:18790",
        ),
    )

    exit_code = cli.main(["gateway", "status"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Gateway 状态: running" in output
    assert "test-token" not in output


def test_gateway_status_returns_error_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gateway 不可用时，CLI 返回明确失败信息。"""
    monkeypatch.setattr(
        cli,
        "load_gateway_config",
        lambda: GatewayConfig(
            host="127.0.0.1",
            port=18790,
            token="test-token-with-at-least-thirty-two-characters",
        ),
    )

    def raise_gateway_error(
        _config: GatewayConfig,
    ) -> GatewayStatus:
        raise GatewayClientError("Gateway 不可用")

    monkeypatch.setattr(
        cli,
        "get_gateway_status",
        raise_gateway_error,
    )

    exit_code = cli.main(["gateway", "status"])

    assert exit_code == 1
    assert "Gateway 状态查询失败" in capsys.readouterr().out


def test_gateway_run_delegates_to_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启动命令只能委托服务模块，不能直接创建 Agent。"""
    config = GatewayConfig(
        host="127.0.0.1",
        port=18790,
        token="test-token-with-at-least-thirty-two-characters",
    )
    called_configs: list[GatewayConfig] = []

    monkeypatch.setattr(
        cli,
        "load_gateway_config",
        lambda: config,
    )
    monkeypatch.setattr(
        cli,
        "configure_logging",
        lambda: None,
    )
    monkeypatch.setattr(
        cli,
        "serve_gateway",
        lambda received_config: called_configs.append(
            received_config
        ),
    )

    exit_code = cli.main(["gateway", "run"])

    assert exit_code == 0
    assert called_configs == [config]


def test_chat_sends_message_only_through_gateway_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """chat 命令只委托 Gateway 客户端，不直接运行 Agent。"""
    config = GatewayConfig(
        host="127.0.0.1",
        port=18790,
        token="test-token-with-at-least-thirty-two-characters",
    )
    received_arguments: list[tuple[GatewayConfig, str, str]] = []

    monkeypatch.setattr(
        cli,
        "load_gateway_config",
        lambda: config,
    )
    monkeypatch.setattr(
        cli,
        "send_gateway_message",
        lambda received_config, session_id, text: (
            received_arguments.append(
                (received_config, session_id, text)
            )
            or GatewayMessageResult(
                reply="测试回答",
                compressed_message_count=0,
            )
        ),
    )

    exit_code = cli.main(
        [
            "chat",
            "测试消息",
            "--session-id",
            "local:cli-test",
        ]
    )

    assert exit_code == 0
    assert received_arguments == [
        (config, "local:cli-test", "测试消息")
    ]
    assert "Agent: 测试回答" in capsys.readouterr().out


def test_chat_returns_safe_error_when_gateway_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gateway 请求失败时，chat 命令不得改为直接调用 Agent。"""
    monkeypatch.setattr(
        cli,
        "load_gateway_config",
        lambda: GatewayConfig(
            host="127.0.0.1",
            port=18790,
            token="test-token-with-at-least-thirty-two-characters",
        ),
    )

    def raise_gateway_error(
        _config: GatewayConfig,
        _session_id: str,
        _text: str,
    ) -> GatewayMessageResult:
        raise GatewayClientError("Gateway 不可用")

    monkeypatch.setattr(
        cli,
        "send_gateway_message",
        raise_gateway_error,
    )

    exit_code = cli.main(["chat", "测试消息"])

    assert exit_code == 1
    assert "消息发送失败" in capsys.readouterr().out


def test_sessions_list_uses_gateway_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会话列表命令只能委托 Gateway 客户端。"""
    config = GatewayConfig(
        host="127.0.0.1",
        port=18790,
        token="test-token-with-at-least-thirty-two-characters",
    )
    received_configs: list[GatewayConfig] = []

    monkeypatch.setattr(
        cli,
        "load_gateway_config",
        lambda: config,
    )
    monkeypatch.setattr(
        cli,
        "list_gateway_sessions",
        lambda received_config: (
            received_configs.append(received_config)
            or [
                GatewaySessionInfo(
                    session_id="local:cli-test",
                    message_count=2,
                    has_summary=True,
                    updated_at="2026-03-07T00:00:00+00:00",
                )
            ]
        ),
    )

    exit_code = cli.main(["sessions", "list"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert received_configs == [config]
    assert "local:cli-test" in output
    assert "消息数: 2" in output
    assert "已有摘要: 是" in output
    assert "test-token" not in output


def test_logs_uses_gateway_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """日志命令只能读取 Gateway 返回的脱敏事件。"""
    config = GatewayConfig(
        host="127.0.0.1",
        port=18790,
        token="test-token-with-at-least-thirty-two-characters",
    )
    received_arguments: list[tuple[GatewayConfig, int]] = []

    monkeypatch.setattr(
        cli,
        "load_gateway_config",
        lambda: config,
    )
    monkeypatch.setattr(
        cli,
        "get_gateway_logs",
        lambda received_config, limit: (
            received_arguments.append((received_config, limit))
            or GatewayLogResult(
                events=["Gateway HTTP 请求完成"]
            )
        ),
    )

    exit_code = cli.main(["logs", "--limit", "3"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert received_arguments == [(config, 3)]
    assert "Gateway HTTP 请求完成" in output
    assert "test-token" not in output
