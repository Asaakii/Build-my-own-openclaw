import pytest

from config import load_gateway_config


def configure_valid_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设置独立测试配置，不读取真实本机 Token。"""
    monkeypatch.setenv(
        "MYCLAW_GATEWAY_HOST",
        "127.0.0.1",
    )
    monkeypatch.setenv(
        "MYCLAW_GATEWAY_PORT",
        "18790",
    )
    monkeypatch.setenv(
        "MYCLAW_GATEWAY_TOKEN",
        "test-token-with-at-least-thirty-two-characters",
    )


def test_valid_gateway_config_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """合法本机配置应整理为 GatewayConfig。"""
    configure_valid_gateway(monkeypatch)

    config = load_gateway_config()

    assert config.host == "127.0.0.1"
    assert config.port == 18790
    assert config.token.startswith("test-token-")


def test_non_local_gateway_host_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不能通过环境变量把 Gateway 监听到局域网或公网。"""
    configure_valid_gateway(monkeypatch)
    monkeypatch.setenv(
        "MYCLAW_GATEWAY_HOST",
        "0.0.0.0",
    )

    with pytest.raises(ValueError, match="127.0.0.1"):
        load_gateway_config()


def test_invalid_gateway_port_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无效端口必须在服务启动前被配置层拒绝。"""
    configure_valid_gateway(monkeypatch)
    monkeypatch.setenv(
        "MYCLAW_GATEWAY_PORT",
        "0",
    )

    with pytest.raises(ValueError, match="1024 到 65535"):
        load_gateway_config()