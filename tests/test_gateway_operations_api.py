import json
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from config import GatewayConfig
from gateway_server import create_gateway_server
from sqlite_state_store import SQLiteStateStore


TEST_TOKEN = "test-token-with-at-least-thirty-two-characters"


class FakeRuntimeWithStore:
    """为会话列表接口提供状态层，不调用真实模型。"""

    def __init__(
        self,
        state_store: SQLiteStateStore,
    ) -> None:
        self.state_store = state_store


@contextmanager
def running_gateway(
    state_store: SQLiteStateStore,
    log_file: Path,
):
    """启动带临时状态和日志的 Gateway，结束后确保关闭。"""
    server = create_gateway_server(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            token=TEST_TOKEN,
        ),
        FakeRuntimeWithStore(state_store),
        log_file,
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
    token: str | None = TEST_TOKEN,
) -> tuple[int, dict[str, object]]:
    """发送只读请求，并统一读取成功或错误 JSON。"""
    headers: dict[str, str] = {}

    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(
        url,
        headers=headers,
        method="GET",
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


def test_session_list_returns_metadata_without_message_content(
    tmp_path: Path,
) -> None:
    """会话列表只能返回元数据，不能泄露任意历史正文。"""
    state_store = SQLiteStateStore(tmp_path / "state.db")
    private_content = "这段正文不应出现在会话列表接口"
    state_store.append_messages(
        "local:metadata-test",
        [{"role": "user", "content": private_content}],
    )

    with running_gateway(
        state_store,
        tmp_path / "gateway.log",
    ) as base_url:
        status_code, payload = request_json(
            f"{base_url}/sessions",
        )

    assert status_code == 200
    assert json.dumps(payload, ensure_ascii=False).find(
        private_content
    ) == -1
    assert payload["sessions"] == [
        {
            "session_id": "local:metadata-test",
            "message_count": 1,
            "has_summary": False,
            "updated_at": payload["sessions"][0]["updated_at"],
        }
    ]


def test_session_list_requires_token(tmp_path: Path) -> None:
    """会话元数据同样属于 Gateway 受保护资源。"""
    state_store = SQLiteStateStore(tmp_path / "state.db")

    with running_gateway(
        state_store,
        tmp_path / "gateway.log",
    ) as base_url:
        status_code, payload = request_json(
            f"{base_url}/sessions",
            token=None,
        )

    assert status_code == 401
    assert payload == {"error": "unauthorized"}


def test_log_list_returns_only_desensitized_gateway_events(
    tmp_path: Path,
) -> None:
    """日志接口不得返回原始路径、工具名或其他模块的内容。"""
    state_store = SQLiteStateStore(tmp_path / "state.db")
    log_file = tmp_path / "gateway.log"
    log_file.write_text(
        "2026-03-07 - INFO - gateway_server - "
        "Gateway HTTP 请求完成: method=POST "
        "path=/sessions/local:private/messages\n"
        "2026-03-07 - INFO - gateway_agent_runtime - "
        "Gateway Agent 请求工具: tool_name=calculate\n"
        "2026-03-07 - INFO - llm_client - "
        "PRIVATE_MARKER_DO_NOT_EXPOSE\n",
        encoding="utf-8",
    )

    with running_gateway(state_store, log_file) as base_url:
        status_code, payload = request_json(
            f"{base_url}/logs?limit=2",
        )

    assert status_code == 200
    assert payload == {
        "events": [
            "2026-03-07 - INFO - gateway_server - "
            "Gateway HTTP 请求完成",
            "2026-03-07 - INFO - gateway_agent_runtime - "
            "Gateway Agent 运行事件",
        ]
    }
    serialized_payload = json.dumps(payload, ensure_ascii=False)
    assert "local:private" not in serialized_payload
    assert "calculate" not in serialized_payload
    assert "PRIVATE_MARKER_DO_NOT_EXPOSE" not in serialized_payload


def test_log_list_rejects_excessive_limit(tmp_path: Path) -> None:
    """日志数量超过上限时应被服务端拒绝。"""
    state_store = SQLiteStateStore(tmp_path / "state.db")

    with running_gateway(
        state_store,
        tmp_path / "gateway.log",
    ) as base_url:
        status_code, payload = request_json(
            f"{base_url}/logs?limit=101",
        )

    assert status_code == 400
    assert payload == {"error": "invalid_log_limit"}
