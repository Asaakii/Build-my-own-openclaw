import pytest

from myclaw import cli


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