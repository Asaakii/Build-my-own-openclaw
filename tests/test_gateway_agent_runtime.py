from pathlib import Path

import pytest

import gateway_agent_runtime
from gateway_agent_runtime import (
    GatewayAgentError,
    GatewayAgentRuntime,
)
from llm_client import LLMClientError
from sqlite_state_store import SQLiteStateStore


def create_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> GatewayAgentRuntime:
    """使用临时数据库和假人格，避免接触真实运行数据。"""
    monkeypatch.setattr(
        gateway_agent_runtime,
        "load_soul",
        lambda: "测试人格",
    )

    return GatewayAgentRuntime(
        SQLiteStateStore(tmp_path / "gateway-test.db")
    )


def test_gateway_runtime_isolates_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """不同 session_id 的消息必须保存到不同 SQLite 会话。"""
    runtime = create_runtime(monkeypatch, tmp_path)

    def fake_agent_turn(
        messages: list[dict[str, object]],
        _authorized_memory: str | None,
        _on_tool_start,
        _allowed_tool_names,
        _on_tool_denied,
    ) -> str:
        answer = f"回答：{messages[-1]['content']}"
        messages.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )
        return answer

    monkeypatch.setattr(
        gateway_agent_runtime,
        "run_agent_turn",
        fake_agent_turn,
    )

    first_result = runtime.handle_text(
        "local:first",
        "第一条消息",
    )
    second_result = runtime.handle_text(
        "local:second",
        "第二条消息",
    )

    store = runtime.state_store

    assert first_result.reply == "回答：第一条消息"
    assert second_result.reply == "回答：第二条消息"
    assert store.load_session("local:first").messages == [
        {"role": "user", "content": "第一条消息"},
        {"role": "assistant", "content": "回答：第一条消息"},
    ]
    assert store.load_session("local:second").messages == [
        {"role": "user", "content": "第二条消息"},
        {"role": "assistant", "content": "回答：第二条消息"},
    ]


def test_failed_model_turn_is_not_saved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """模型失败时，不得向 SQLite 留下半截用户消息。"""
    runtime = create_runtime(monkeypatch, tmp_path)

    def raise_model_error(
        _messages: list[dict[str, object]],
        _authorized_memory: str | None,
        _on_tool_start,
        _allowed_tool_names,
        _on_tool_denied,
    ) -> str:
        raise LLMClientError("测试失败")

    monkeypatch.setattr(
        gateway_agent_runtime,
        "run_agent_turn",
        raise_model_error,
    )

    with pytest.raises(GatewayAgentError):
        runtime.handle_text("local:failed", "不会保存")

    loaded_session = runtime.state_store.load_session(
        "local:failed"
    )
    assert loaded_session.messages == []
    assert loaded_session.summary is None


def test_gateway_runtime_passes_restricted_tool_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Gateway 运行器必须把代码级工具白名单传给 Agent 循环。"""
    runtime = create_runtime(monkeypatch, tmp_path)
    received_policy: list[object] = []

    def fake_agent_turn(
        messages: list[dict[str, object]],
        _authorized_memory: str | None,
        _on_tool_start,
        allowed_tool_names,
        _on_tool_denied,
    ) -> str:
        received_policy.append(allowed_tool_names)
        messages.append({"role": "assistant", "content": "策略已启用"})
        return "策略已启用"

    monkeypatch.setattr(
        gateway_agent_runtime,
        "run_agent_turn",
        fake_agent_turn,
    )

    result = runtime.handle_text("local:policy", "测试策略")

    assert result.reply == "策略已启用"
    assert "write_note" not in received_policy[0]
    assert "calculate" in received_policy[0]
