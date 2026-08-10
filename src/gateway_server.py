import hmac
import json
import logging
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from config import GatewayConfig, load_reminder_config
from gateway_agent_runtime import (
    GatewayAgentError,
    GatewayAgentRuntime,
)
from logging_config import LOG_FILE
from persistent_reminder_service import (
    PersistentReminderService,
    ReminderTaskError,
)
from sqlite_state_store import SQLiteStateStore, StateStoreError


logger = logging.getLogger(__name__)

PACKAGE_NAME = "myclaw"

MAX_REQUEST_BODY_BYTES = 8_000
MAX_MESSAGE_CHARACTERS = 2_000
DEFAULT_LOG_EVENT_LIMIT = 20
MAX_LOG_EVENT_LIMIT = 100
MAX_LOG_READ_BYTES = 256_000
SESSION_MESSAGE_PATH_PATTERN = re.compile(
    r"^/sessions/([A-Za-z0-9][A-Za-z0-9:_-]{0,119})/messages$"
)
REMINDER_TASK_PATH = "/tasks/reminders"


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
        log_file: Path = LOG_FILE,
        reminder_service: PersistentReminderService | None = None,
    ) -> None:
        super().__init__(
            (config.host, config.port),
            GatewayRequestHandler,
        )
        self.gateway_config = config
        self.agent_runtime = agent_runtime
        self.log_file = log_file
        self.reminder_service = reminder_service
        self.started_at = datetime.now(
            timezone.utc
        ).isoformat()

    def get_diagnostics(self) -> dict[str, str]:
        """返回最小运行诊断；不访问会话正文、任务正文或私密配置。"""
        diagnostics = {
            "agent_runtime": (
                "ready"
                if self.agent_runtime is not None
                else "unavailable"
            ),
            "state_store": "unavailable",
            "reminder_service": (
                "ready"
                if self.reminder_service is not None
                else "unavailable"
            ),
            "tool_policy": "restricted",
        }

        if self.agent_runtime is None:
            return diagnostics

        try:
            # 只初始化并验证最小状态层，不读取任何会话或任务内容。
            self.agent_runtime.state_store.initialize()
        except (AttributeError, StateStoreError) as error:
            logger.warning(
                "Gateway 状态诊断失败: error_type=%s",
                type(error).__name__,
            )
            diagnostics["state_store"] = "degraded"
            return diagnostics

        diagnostics["state_store"] = "ready"
        return diagnostics


class GatewayRequestHandler(BaseHTTPRequestHandler):
    """处理已经过 Token 保护的 Gateway 最小接口。"""

    server_version = "MyClawGateway"
    sys_version = ""

    def do_GET(self) -> None:
        """处理健康检查和状态查询。"""
        if not self.is_authorized():
            return

        request_url = urlsplit(self.path)
        path = request_url.path

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
                    "diagnostics": self.server.get_diagnostics(),
                },
            )
            return

        if path == "/sessions":
            self.handle_session_list()
            return

        if path == "/logs":
            self.handle_log_list(request_url.query)
            return

        if path == "/tasks":
            self.handle_task_list()
            return

        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        """处理受限的会话消息请求。"""
        if not self.is_authorized():
            return

        path = urlsplit(self.path).path

        # /health 和 /status 是已知只读接口。
        # 使用错误方法访问时应返回 405，而不是伪装成不存在。
        if path in {"/health", "/status", "/sessions", "/logs", "/tasks"}:
            self.send_json(
                405,
                {"error": "method_not_allowed"},
            )
            return

        if path == REMINDER_TASK_PATH:
            self.handle_create_reminder()
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

    def handle_task_list(self) -> None:
        """返回任务元数据，绝不返回提醒正文或投递聊天标识。"""
        if self.server.reminder_service is None:
            self.send_json(
                503,
                {"error": "task_service_unavailable"},
            )
            return

        try:
            tasks = self.server.reminder_service.list_tasks()
        except StateStoreError as error:
            logger.warning(
                "Gateway 任务列表读取失败: error_type=%s",
                type(error).__name__,
            )
            self.send_json(
                500,
                {"error": "task_list_unavailable"},
            )
            return

        self.send_json(
            200,
            {
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "session_id": task.session_id,
                        "task_type": task.task_type,
                        "status": task.status,
                        "due_at": task.due_at,
                        "updated_at": task.updated_at,
                    }
                    for task in tasks
                ]
            },
        )

    def handle_create_reminder(self) -> None:
        """经 Gateway 创建持久提醒，渠道进程不再自行持有定时器。"""
        if self.server.reminder_service is None:
            self.send_json(
                503,
                {"error": "task_service_unavailable"},
            )
            return

        payload = self.read_reminder_payload()

        if payload is None:
            return

        try:
            result = self.server.reminder_service.create_reminder(
                session_id=payload["session_id"],
                delay_seconds=payload["delay_seconds"],
                content=payload["content"],
                delivery=payload["delivery"],
            )
        except (ReminderTaskError, StateStoreError) as error:
            logger.warning(
                "Gateway 提醒创建失败: error_type=%s",
                type(error).__name__,
            )
            self.send_json(
                400,
                {"error": "invalid_reminder_request"},
            )
            return

        self.send_json(
            201,
            {
                "task_id": result.task_id,
                "due_at": result.due_at,
                "status": result.status,
            },
        )

    def handle_session_list(self) -> None:
        """仅返回会话元数据，绝不读取或返回消息正文。"""
        if self.server.agent_runtime is None:
            self.send_json(
                503,
                {"error": "session_service_unavailable"},
            )
            return

        try:
            sessions = self.server.agent_runtime.state_store.list_sessions()
        except StateStoreError as error:
            logger.warning(
                "Gateway 会话列表读取失败: error_type=%s",
                type(error).__name__,
            )
            self.send_json(
                500,
                {"error": "session_list_unavailable"},
            )
            return

        self.send_json(
            200,
            {
                "sessions": [
                    {
                        "session_id": session.session_id,
                        "message_count": session.message_count,
                        "has_summary": session.has_summary,
                        "updated_at": session.updated_at,
                    }
                    for session in sessions
                ]
            },
        )

    def handle_log_list(self, query: str) -> None:
        """返回数量受限且已脱敏的 Gateway 运行事件。"""
        limit = self.read_log_limit(query)

        if limit is None:
            return

        try:
            events = read_recent_gateway_logs(
                self.server.log_file,
                limit,
            )
        except GatewayServerError as error:
            logger.warning(
                "Gateway 日志读取失败: error_type=%s",
                type(error).__name__,
            )
            self.send_json(
                500,
                {"error": "log_list_unavailable"},
            )
            return

        self.send_json(200, {"events": events})

    def read_log_limit(self, query: str) -> int | None:
        """校验日志数量，防止一次读取过多内容。"""
        if not query:
            return DEFAULT_LOG_EVENT_LIMIT

        parameters = parse_qs(
            query,
            keep_blank_values=True,
        )

        if (
            set(parameters) != {"limit"}
            or len(parameters["limit"]) != 1
        ):
            self.send_json(
                400,
                {"error": "invalid_log_limit"},
            )
            return None

        raw_limit = parameters["limit"][0]

        if not raw_limit.isdecimal():
            self.send_json(
                400,
                {"error": "invalid_log_limit"},
            )
            return None

        limit = int(raw_limit)

        if not 1 <= limit <= MAX_LOG_EVENT_LIMIT:
            self.send_json(
                400,
                {"error": "invalid_log_limit"},
            )
            return None

        return limit

    def read_json_payload(self) -> dict[str, object] | None:
        """读取 JSON 对象请求体，通用边界检查不回显用户输入。"""
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

        if not isinstance(payload, dict):
            self.send_json(
                400,
                {"error": "invalid_json_payload"},
            )
            return None

        return payload

    def read_message_text(self) -> str | None:
        """读取并严格校验会话消息 JSON，不回显用户输入。"""
        payload = self.read_json_payload()

        if payload is None:
            return None

        if set(payload) != {"text"} or not isinstance(payload["text"], str):
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

    def read_reminder_payload(self) -> dict[str, object] | None:
        """严格限制提醒创建参数，避免任意任务类型进入调度器。"""
        payload = self.read_json_payload()

        if payload is None:
            return None

        required_fields = {
            "session_id",
            "delay_seconds",
            "content",
            "delivery",
        }

        if (
            set(payload) != required_fields
            or not isinstance(payload["session_id"], str)
            or not isinstance(payload["delay_seconds"], int)
            or isinstance(payload["delay_seconds"], bool)
            or not isinstance(payload["content"], str)
            or not isinstance(payload["delivery"], dict)
        ):
            self.send_json(
                400,
                {"error": "invalid_reminder_payload"},
            )
            return None

        return payload

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
        payload: dict[str, object],
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
        """只记录脱敏路由类别，不记录会话标识、请求头或正文。"""
        path = urlsplit(self.path).path

        if SESSION_MESSAGE_PATH_PATTERN.fullmatch(path):
            route = "session_message"
        elif path in {
            "/health",
            "/status",
            "/sessions",
            "/logs",
            "/tasks",
            REMINDER_TASK_PATH,
        }:
            route = path
        else:
            route = "unknown"

        logger.info(
            "Gateway HTTP 请求完成: method=%s route=%s",
            self.command,
            route,
        )


def sanitize_gateway_log_line(line: str) -> str | None:
    """只保留允许展示的 Gateway 事件类别，不保留动态参数。"""
    server_marker = " - gateway_server - "
    runtime_marker = " - gateway_agent_runtime - "
    reminder_marker = " - persistent_reminder_service - "

    if server_marker in line:
        prefix, _, event = line.partition(server_marker)

        if event.startswith("Gateway 启动:"):
            return f"{prefix}{server_marker}Gateway 启动"

        if event.startswith("Gateway 已停止"):
            return f"{prefix}{server_marker}Gateway 已停止"

        if event.startswith("Gateway HTTP 请求完成:"):
            return (
                f"{prefix}{server_marker}"
                "Gateway HTTP 请求完成"
            )

        if event.startswith("Gateway 消息处理失败:"):
            return (
                f"{prefix}{server_marker}"
                "Gateway 消息处理失败"
            )

        if event.startswith("Gateway 提醒创建失败:"):
            return (
                f"{prefix}{server_marker}"
                "Gateway 提醒创建失败"
            )

        if event.startswith("Gateway 状态诊断失败:"):
            return (
                f"{prefix}{server_marker}"
                "Gateway 状态诊断失败"
            )

    if runtime_marker in line:
        prefix, _, event = line.partition(runtime_marker)
        allowed_prefixes = (
            "Gateway Agent 请求工具:",
            "Gateway Agent 工具策略拒绝",
            "Gateway Agent 回合失败:",
            "Gateway Agent 保存会话失败:",
            "Gateway 会话压缩失败:",
            "Gateway 压缩快照保存失败:",
        )

        if event.startswith(allowed_prefixes):
            return (
                f"{prefix}{runtime_marker}"
                "Gateway Agent 运行事件"
            )

    if reminder_marker in line:
        prefix, _, event = line.partition(reminder_marker)
        allowed_prefixes = (
            "Gateway 提醒任务已创建:",
            "Gateway 提醒任务扫描失败:",
            "Gateway 提醒任务发送失败:",
            "Gateway 提醒任务异常:",
            "Gateway 提醒任务状态更新失败:",
            "Gateway 提醒任务已发送",
            "Gateway 提醒调度器已启动",
            "Gateway 提醒调度器已停止",
        )

        if event.startswith(allowed_prefixes):
            return (
                f"{prefix}{reminder_marker}"
                "Gateway 提醒任务事件"
            )

    return None


def read_recent_gateway_logs(
    log_file: Path,
    limit: int,
) -> list[str]:
    """读取日志末尾的有限字节，并只返回脱敏后的 Gateway 事件。"""
    if not log_file.exists():
        return []

    try:
        with log_file.open("rb") as file:
            file.seek(0, 2)
            file_size = file.tell()
            start_position = max(
                0,
                file_size - MAX_LOG_READ_BYTES,
            )
            file.seek(start_position)
            content = file.read()
    except OSError as error:
        raise GatewayServerError(
            "无法读取 Gateway 日志。"
        ) from error

    events: list[str] = []

    for line in content.decode(
        "utf-8",
        errors="replace",
    ).splitlines():
        safe_line = sanitize_gateway_log_line(line)

        if safe_line is not None:
            events.append(safe_line)

    return events[-limit:]


def create_gateway_server(
    config: GatewayConfig,
    agent_runtime: GatewayAgentRuntime | None = None,
    log_file: Path = LOG_FILE,
    reminder_service: PersistentReminderService | None = None,
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
        return GatewayHTTPServer(
            config,
            agent_runtime,
            log_file,
            reminder_service,
        )
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
        state_store = SQLiteStateStore()
        agent_runtime = GatewayAgentRuntime(state_store)
        reminder_service = PersistentReminderService(
            state_store,
            load_reminder_config(),
        )
    except (FileNotFoundError, ValueError) as error:
        logger.error(
            "Gateway 运行器初始化失败: error_type=%s",
            type(error).__name__,
        )
        raise GatewayServerError(
            "无法初始化 Gateway Agent 运行器。"
        ) from error

    server = create_gateway_server(
        config,
        agent_runtime,
        reminder_service=reminder_service,
    )

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
        reminder_service.start()
        server.serve_forever(poll_interval=0.5)
    finally:
        reminder_service.stop()
        server.server_close()
        logger.info("Gateway 已停止")
