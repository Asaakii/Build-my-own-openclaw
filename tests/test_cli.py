import pytest

from myclaw import cli
from config import GatewayConfig
from gateway_client import (
    GatewayClientError,
    GatewayStatus,
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