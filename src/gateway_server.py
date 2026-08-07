import hmac
import json
import logging
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import PackageNotFoundError, version

from config import GatewayConfig
from gateway_agent_runtime import (
    GatewayAgentError,
    GatewayAgentRuntime,
)
from sqlite_state_store import SQLiteStateStore


logger = logging.getLogger(__name__)

PACKAGE_NAME = "myclaw"

MAX_REQUEST_BODY_BYTES = 8_000
MAX_MESSAGE_CHARACTERS = 2_000
SESSION_MESSAGE_PATH_PATTERN = re.compile(
    r"^/sessions/([A-Za-z0-9][A-Za-z0-9:_-]{0,119})/messages$"
)


class GatewayServerError(RuntimeError):
    """表示 Gateway 无法安全启动或运行。"""


def get_gateway_version() -> str:
    """读取已安装包版本；未安装时给开发阶段一个明确标记。"""
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "开发版（未安装）"


class GatewayHTTPServer(HTTPServer):
    """保存 Gateway 运行元数据和专用 Agent 运行器。"""

    def __init__(
        self,
        config: GatewayConfig,
        agent_runtime: GatewayAgentRuntime | None = None,
    ) -> None:
        super().__init__(
            (config.host, config.port),
            GatewayRequestHandler,
        )
        self.gateway_config = config
        self.agent_runtime = agent_runtime
        self.started_at = datetime.now(
            timezone.utc
        ).isoformat()


class GatewayRequestHandler(BaseHTTPRequestHandler):
    """处理已经过 Token 保护的 Gateway 最小接口。"""

    server_version = "MyClawGateway"
    sys_version = ""

    def do_GET(self) -> None:
        """处理健康检查和状态查询。"""
        if not self.is_authorized():
            return

        path = self.path.split("?", maxsplit=1)[0]

        if path == "/health":
            self.send_json(200, {"status": "ok"})
            return

        if path == "/status":
            self.send_json(
                200,
                {
                    "status": "running",
                    "version": get_gateway_version(),
                    "started_at": self.server.started_at,
                    "address": (
                        f"{self.server.gateway_config.host}:"
                        f"{self.server.server_port}"
                    ),
                },
            )
            return

        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        """处理受限的会话消息请求。"""
        if not self.is_authorized():
            return

        path = self.path.split("?", maxsplit=1)[0]

        # /health 和 /status 是已知只读接口。
        # 使用错误方法访问时应返回 405，而不是伪装成不存在。
        if path in {"/health", "/status"}:
            self.send_json(
                405,
                {"error": "method_not_allowed"},
            )
            return

        matched_path = SESSION_MESSAGE_PATH_PATTERN.fullmatch(
            path
        )

        if matched_path is None:
            self.send_json(404, {"error": "not_found"})
            return

        text = self.read_message_text()

        if text is None:
            return

        if self.server.agent_runtime is None:
            self.send_json(
                503,
                {"error": "message_service_unavailable"},
            )
            return

        session_id = matched_path.group(1)

        try:
            result = self.server.agent_runtime.handle_text(
                session_id,
                text,
            )
        except GatewayAgentError as error:
            logger.warning(
                "Gateway 消息处理失败: error_type=%s",
                type(error).__name__,
            )
            self.send_json(
                500,
                {"error": "agent_request_failed"},
            )
            return

        self.send_json(
            200,
            {
                "reply": result.reply,
                "compressed_message_count": str(
                    result.compressed_message_count
                ),
            },
        )

    def read_message_text(self) -> str | None:
        """读取并严格校验 JSON 请求体，不回显用户输入。"""
        content_type = self.headers.get(
            "Content-Type",
            "",
        ).split(";", maxsplit=1)[0].strip().lower()

        if content_type != "application/json":
            self.send_json(
                415,
                {"error": "unsupported_media_type"},
            )
            return None

        raw_content_length = self.headers.get(
            "Content-Length",
        )

        if raw_content_length is None:
            self.send_json(
                411,
                {"error": "content_length_required"},
            )
            return None

        try:
            content_length = int(raw_content_length)
        except ValueError:
            self.send_json(
                400,
                {"error": "invalid_content_length"},
            )
            return None

        if content_length < 0:
            self.send_json(
                400,
                {"error": "invalid_content_length"},
            )
            return None

        if content_length > MAX_REQUEST_BODY_BYTES:
            self.send_json(
                413,
                {"error": "request_too_large"},
            )
            return None

        raw_body = self.rfile.read(content_length)

        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(
                400,
                {"error": "invalid_json"},
            )
            return None

        if (
            not isinstance(payload, dict)
            or set(payload) != {"text"}
            or not isinstance(payload["text"], str)
        ):
            self.send_json(
                400,
                {"error": "invalid_message_payload"},
            )
            return None

        text = payload["text"].strip()

        if not text:
            self.send_json(
                400,
                {"error": "message_empty"},
            )
            return None

        if len(text) > MAX_MESSAGE_CHARACTERS:
            self.send_json(
                413,
                {"error": "message_too_long"},
            )
            return None

        return text

    def is_authorized(self) -> bool:
        """在任何路由处理前校验 Token。"""
        provided_value = self.headers.get(
            "Authorization",
            "",
        )
        expected_value = (
            f"Bearer {self.server.gateway_config.token}"
        )

        if hmac.compare_digest(
            provided_value,
            expected_value,
        ):
            return True

        self.send_json(401, {"error": "unauthorized"})
        return False

    def send_json(
        self,
        status_code: int,
        payload: dict[str, str],
    ) -> None:
        """返回固定 JSON，不回显 Token 或请求正文。"""
        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status_code)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(
        self,
        _format: str,
        *_arguments: object,
    ) -> None:
        """只记录方法和路径，不记录请求头或正文。"""
        path = self.path.split("?", maxsplit=1)[0]
        logger.info(
            "Gateway HTTP 请求完成: method=%s path=%s",
            self.command,
            path,
        )


def create_gateway_server(
    config: GatewayConfig,
    agent_runtime: GatewayAgentRuntime | None = None,
) -> GatewayHTTPServer:
    """创建仅绑定回环地址的 Gateway 服务实例。"""
    if config.host != "127.0.0.1":
        raise GatewayServerError(
            "Gateway 只能监听 127.0.0.1。"
        )

    if not 0 <= config.port <= 65535:
        raise GatewayServerError("Gateway 端口无效。")

    if len(config.token) < 32:
        raise GatewayServerError("Gateway Token 无效。")

    try:
        return GatewayHTTPServer(config, agent_runtime)
    except OSError as error:
        logger.error(
            "Gateway 启动失败: error_type=%s",
            type(error).__name__,
        )
        raise GatewayServerError(
            "无法启动 Gateway，请检查端口是否被占用。"
        ) from error


def serve_gateway(
    config: GatewayConfig,
) -> None:
    """初始化专用运行器后，以前台方式运行 Gateway。"""
    try:
        agent_runtime = GatewayAgentRuntime(
            SQLiteStateStore()
        )
    except (FileNotFoundError, ValueError) as error:
        logger.error(
            "Gateway 运行器初始化失败: error_type=%s",
            type(error).__name__,
        )
        raise GatewayServerError(
            "无法初始化 Gateway Agent 运行器。"
        ) from error

    server = create_gateway_server(config, agent_runtime)

    logger.info(
        "Gateway 启动: host=%s port=%d",
        config.host,
        server.server_port,
    )
    print(
        "Gateway 已启动。按 Ctrl+C 停止。\n"
        f"监听地址: http://{config.host}:{server.server_port}"
    )

    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        logger.info("Gateway 已停止")