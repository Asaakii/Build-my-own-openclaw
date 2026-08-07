import hmac
import json
import logging
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import PackageNotFoundError, version

from config import GatewayConfig


logger = logging.getLogger(__name__)

PACKAGE_NAME = "myclaw"


class GatewayServerError(RuntimeError):
    """表示 Gateway 无法安全启动或运行。"""


def get_gateway_version() -> str:
    """读取已安装包版本；开发环境无法读取时使用明确标记。"""
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "开发版（未安装）"


class GatewayHTTPServer(HTTPServer):
    """保存 Gateway 运行元数据的本机 HTTP 服务。"""

    def __init__(
        self,
        config: GatewayConfig,
    ) -> None:
        super().__init__(
            (config.host, config.port),
            GatewayRequestHandler,
        )
        self.gateway_config = config
        self.started_at = datetime.now(
            timezone.utc
        ).isoformat()


class GatewayRequestHandler(BaseHTTPRequestHandler):
    """只提供受 Token 保护的健康和状态接口。"""

    server_version = "MyClawGateway"
    sys_version = ""

    def do_GET(self) -> None:
        """处理当前允许的只读请求。"""
        if not self.is_authorized():
            return

        path = self.path.split("?", maxsplit=1)[0]

        if path == "/health":
            self.send_json(
                200,
                {"status": "ok"},
            )
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

        self.send_json(
            404,
            {"error": "not_found"},
        )

    def do_POST(self) -> None:
        """消息接口尚未实现，先明确拒绝所有写请求。"""
        if not self.is_authorized():
            return

        self.send_json(
            405,
            {"error": "method_not_allowed"},
        )

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

        self.send_json(
            401,
            {"error": "unauthorized"},
        )
        return False

    def send_json(
        self,
        status_code: int,
        payload: dict[str, str],
    ) -> None:
        """以 JSON 返回固定状态信息，不回显请求内容或 Token。"""
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
        """覆盖默认日志，避免记录请求头和潜在敏感内容。"""
        path = self.path.split("?", maxsplit=1)[0]
        logger.info(
            "Gateway HTTP 请求完成: method=%s path=%s",
            self.command,
            path,
        )


def create_gateway_server(
    config: GatewayConfig,
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
        return GatewayHTTPServer(config)
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
    """以前台方式运行 Gateway，并在停止时关闭监听端口。"""
    server = create_gateway_server(config)

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