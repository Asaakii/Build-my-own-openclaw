from types import SimpleNamespace

import pytest

import llm_client
from gateway_tool_policy import GATEWAY_ALLOWED_TOOL_NAMES


def tool_call(name: str, arguments: str) -> SimpleNamespace:
    """构造 OpenAI 兼容的最小工具调用对象。"""
    return SimpleNamespace(
        id="call-test",
        function=SimpleNamespace(
            name=name,
            arguments=arguments,
        ),
    )


def test_gateway_policy_excludes_local_file_write_tool() -> None:
    """Gateway 的默认最小策略不允许模型写入本地笔记。"""
    assert "write_note" not in GATEWAY_ALLOWED_TOOL_NAMES
    assert "calculate" in GATEWAY_ALLOWED_TOOL_NAMES
    assert "save_memory" in GATEWAY_ALLOWED_TOOL_NAMES


def test_agent_turn_rejects_tool_outside_gateway_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使模型伪造越权调用，执行阶段也必须拒绝且不触碰工具实现。"""
    responses = iter(
        [
            SimpleNamespace(
                content=None,
                tool_calls=[
                    tool_call(
                        "write_note",
                        '{"filename":"unsafe.md","content":"unsafe"}',
                    )
                ],
            ),
            SimpleNamespace(
                content="越权工具已被拒绝",
                tool_calls=[],
            ),
        ]
    )
    received_tool_names: list[set[str]] = []
    denied_count = 0

    def fake_request_completion(
        _client: object,
        _config: object,
        _messages: list[dict[str, object]],
        use_tools: bool = True,
        tool_definitions: list[dict[str, object]] | None = None,
    ) -> SimpleNamespace:
        assert use_tools is True
        received_tool_names.append(
            {
                definition["function"]["name"]
                for definition in tool_definitions or []
            }
        )
        return next(responses)

    def mark_denied() -> None:
        nonlocal denied_count
        denied_count += 1

    monkeypatch.setattr(
        llm_client,
        "load_model_config",
        lambda: object(),
    )
    monkeypatch.setattr(
        llm_client,
        "create_deepseek_client",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        llm_client,
        "request_completion",
        fake_request_completion,
    )
    monkeypatch.setattr(
        llm_client,
        "execute_tool",
        lambda *_arguments: pytest.fail("越权工具不应执行"),
    )

    messages: list[dict[str, object]] = [
        {"role": "user", "content": "测试"}
    ]
    answer = llm_client.run_agent_turn(
        messages,
        allowed_tool_names={"calculate"},
        on_tool_denied=mark_denied,
    )

    assert answer == "越权工具已被拒绝"
    assert received_tool_names == [{"calculate"}, {"calculate"}]
    assert denied_count == 1
    assert messages[-2]["content"] == (
        "工具执行失败：当前工具策略不允许此工具"
    )
