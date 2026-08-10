import argparse
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

from config import (
    describe_model_config,
    load_gateway_config,
)
from gateway_client import (
    GatewayClientError,
    get_gateway_logs,
    get_gateway_status,
    GatewayMessageResult,
    list_gateway_sessions,
    send_gateway_message,
)
from gateway_server import (
    GatewayServerError,
    serve_gateway,
)
from logging_config import configure_logging


PACKAGE_NAME = "myclaw"


def get_installed_version() -> str:
    """读取已安装包的版本；未安装时给开发阶段一个明确标记。"""
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "开发版（未安装）"


def build_parser() -> argparse.ArgumentParser:
    """集中定义 CLI 命令结构，后续 Gateway 命令会继续加在这里。"""
    parser = argparse.ArgumentParser(
        prog=PACKAGE_NAME,
        description="本机个人 Agent 的 Gateway 与 CLI 工具。",
    )

    # argparse 会在处理 --version 后自行输出版本并正常退出。
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_installed_version()}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
    )

    config_parser = subparsers.add_parser(
        "config",
        help="检查本机配置。",
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command",
        required=True,
        metavar="CONFIG_COMMAND",
    )
    config_subparsers.add_parser(
        "check",
        help="检查模型配置，并隐藏 API Key。",
    )

    gateway_parser = subparsers.add_parser(
        "gateway",
        help="管理本机 Gateway。",
    )
    gateway_subparsers = gateway_parser.add_subparsers(
        dest="gateway_command",
        required=True,
        metavar="GATEWAY_COMMAND",
    )
    gateway_subparsers.add_parser(
        "run",
        help="以前台方式启动本机 Gateway。",
    )
    gateway_subparsers.add_parser(
        "status",
        help="查询本机 Gateway 状态。",
    )

    chat_parser = subparsers.add_parser(
        "chat",
        help="通过 Gateway 向指定会话发送一条文字消息。",
    )
    chat_parser.add_argument(
        "text",
        help="要发送给 Agent 的文字消息。",
    )
    chat_parser.add_argument(
        "--session-id",
        default="local:default",
        help="会话标识，默认使用 local:default。",
    )

    sessions_parser = subparsers.add_parser(
        "sessions",
        help="通过 Gateway 查看会话元数据。",
    )
    sessions_subparsers = sessions_parser.add_subparsers(
        dest="sessions_command",
        required=True,
        metavar="SESSIONS_COMMAND",
    )
    sessions_subparsers.add_parser(
        "list",
        help="列出会话，不显示消息正文。",
    )

    logs_parser = subparsers.add_parser(
        "logs",
        help="通过 Gateway 查看脱敏运行日志。",
    )
    logs_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="返回的日志事件数量，范围由 Gateway 限制。",
    )

    return parser


def run_config_check() -> int:
    """复用已有的安全配置摘要，绝不输出真实 API Key。"""
    try:
        print(describe_model_config())
    except ValueError as error:
        print(f"配置检查失败: {error}")
        return 1

    return 0


def run_gateway() -> int:
    """加载本机配置并以前台方式启动 Gateway。"""
    try:
        config = load_gateway_config()
    except ValueError as error:
        print(f"Gateway 配置失败: {error}")
        return 1

    # Gateway 的启动、停止和请求事件写入本机脱敏日志。
    configure_logging()

    try:
        serve_gateway(config)
    except GatewayServerError as error:
        print(f"Gateway 启动失败: {error}")
        return 1
    except KeyboardInterrupt:
        print("Gateway 已停止。")
        return 0

    return 0


def run_gateway_status() -> int:
    """通过 Gateway 客户端查询状态，绝不在 CLI 中直接读取状态层。"""
    try:
        config = load_gateway_config()
        status = get_gateway_status(config)
    except (ValueError, GatewayClientError) as error:
        print(f"Gateway 状态查询失败: {error}")
        return 1

    print(
        "\n".join(
            [
                f"Gateway 状态: {status.status}",
                f"Gateway 版本: {status.version}",
                f"启动时间: {status.started_at}",
                f"监听地址: {status.address}",
            ]
        )
    )
    return 0


def run_chat(
    session_id: str,
    text: str,
) -> int:
    """通过 Gateway 发送消息，CLI 不直接创建 Agent。"""
    try:
        config = load_gateway_config()
        result = send_gateway_message(
            config,
            session_id,
            text,
        )
    except (ValueError, GatewayClientError) as error:
        print(f"消息发送失败: {error}")
        return 1

    print(f"Agent: {result.reply}")

    if result.compressed_message_count:
        print(
            "提示：会话已压缩，"
            f"总结了 {result.compressed_message_count} 条旧消息。"
        )

    return 0


def run_sessions_list() -> int:
    """通过 Gateway 列出会话，不直接打开 SQLite 数据库。"""
    try:
        config = load_gateway_config()
        sessions = list_gateway_sessions(config)
    except (ValueError, GatewayClientError) as error:
        print(f"会话列表查询失败: {error}")
        return 1

    if not sessions:
        print("暂无可用会话。")
        return 0

    print("会话列表：")

    for session in sessions:
        summary_status = "是" if session.has_summary else "否"
        print(
            "- "
            f"{session.session_id}"
            f" | 消息数: {session.message_count}"
            f" | 已有摘要: {summary_status}"
            f" | 更新时间: {session.updated_at}"
        )

    return 0


def run_logs(limit: int) -> int:
    """通过 Gateway 查看脱敏日志，不直接读取本地日志文件。"""
    try:
        config = load_gateway_config()
        result = get_gateway_logs(config, limit)
    except (ValueError, GatewayClientError) as error:
        print(f"日志查询失败: {error}")
        return 1

    if not result.events:
        print("暂无 Gateway 运行事件。")
        return 0

    print("最近 Gateway 运行事件：")

    for event in result.events:
        print(f"- {event}")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """解析 CLI 参数，并执行当前已经支持的最小命令集。"""
    parser = build_parser()
    arguments = parser.parse_args(
        list(argv) if argv is not None else None
    )

    if (
        arguments.command == "config"
        and arguments.config_command == "check"
    ):
        return run_config_check()

    if (
        arguments.command == "gateway"
        and arguments.gateway_command == "run"
    ):
        return run_gateway()

    if (
        arguments.command == "gateway"
        and arguments.gateway_command == "status"
    ):
        return run_gateway_status()

    if arguments.command == "chat":
        return run_chat(
            arguments.session_id,
            arguments.text,
        )

    if (
        arguments.command == "sessions"
        and arguments.sessions_command == "list"
    ):
        return run_sessions_list()

    if arguments.command == "logs":
        return run_logs(arguments.limit)

    # 目前理论上不会到这里；保留此分支便于未来增加命令时安全失败。
    parser.error("不支持的命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
