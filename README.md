# Build My Own OpenClaw

从零学习开发的个人 Agent：调用现成大模型，具备会话记忆、受控工具、Skills，并可在终端和 Telegram 私聊中工作。第二阶段加入了仅本机可访问的 Gateway 与 CLI，使会话、工具和提醒副作用有唯一入口。

> 这是学习型、单用户项目，不是 OpenClaw 官方产品的复刻，也不具备生产级的多用户隔离、高可用、插件生态或沙箱能力。

## 已实现能力

- 使用 DeepSeek 的 OpenAI 兼容接口进行多轮对话。
- 在终端或 Telegram 私聊中接收文字消息。
- 在程序重启后恢复本地会话；历史过长时生成摘要并保留最近消息。
- 仅在使用 `/remember` 明确授权后保存长期记忆，并支持检索。
- 提供受控工具：
  - 当前系统时间；
  - 安全的基础算术计算；
  - 受限目录内的 Markdown 笔记读写；
  - 当前模型天气查询；
  - 本地 Skills 加载。
- 通过 `daily-review` Skill 生成结构化每日复盘。
- 通过本机 Gateway 统一处理 CLI 与 Telegram 的会话、模型和工具请求。
- 通过 Gateway 创建可跨正常重启恢复的 Telegram 定时提醒。
- Gateway 状态提供固定、脱敏的运行诊断；任务与日志接口不返回消息正文或投递地址。
- Gateway 使用代码级工具策略：不向模型开放本地笔记写入工具；长期记忆仍必须经过 `/remember` 的逐字授权校验。
- 使用 pytest 覆盖关键安全边界；当前共有 76 项自动化测试。

## 当前限制

- 仅支持一个 Telegram 白名单用户的私聊文字消息。
- Telegram 渠道必须依赖本机 Gateway；Gateway 未运行时不会退回为直接模型调用。
- 提醒在发送前会进入 `delivering` 状态；若进程在外部发送的临界时刻异常终止，系统不会自动重试，优先避免重复提醒而非承诺严格的“恰好一次”投递。
- Gateway 的工具策略是最小白名单，不是通用沙箱；它不能替代操作系统隔离、网络隔离或生产级授权系统。
- 不支持图片、语音、群聊和其他消息渠道。
- Skills 目前通过说明文本约束工具使用，尚未实现按 Skill 的代码级工具权限隔离。
- 天气结果来自 Open-Meteo 的模型数据，不等同于现实世界的实时实测数据。
- 外部模型、Telegram 和天气服务都可能因网络、配额或服务状态而失败。

## 项目结构

```text
.
├── src/                 # Agent 核心、工具、渠道和运行入口
├── tests/               # 自动化测试
├── skills/              # 可复用技能说明
├── workspace/           # 人格、长期记忆和受限笔记
├── sessions/            # 本地会话记录，不提交 Git
├── .env.example         # 可公开的配置模板
├── requirements.txt     # 直接依赖及版本
└── learning-log.md      # 学习日志
```

## 环境要求

- Python 3.12
- DeepSeek API Key
- 网络连接

如果要使用 Telegram 渠道，还需要：

- 已由 BotFather 创建的 Telegram Bot Token；
- 自己的 Telegram 数字用户 ID。

## 安装

克隆仓库并进入项目目录后，创建并激活虚拟环境：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

以开发模式安装项目和测试依赖：

```bash
python -m pip install -e ".[dev]"
```

复制配置模板：

```bash
cp .env.example .env
```

`.env` 只保存在本机，已经被 Git 忽略。不要把 API Key、Bot Token 或 Telegram 用户 ID 写入源码、README、学习日志或 Git 提交。

## 配置

在 `.env` 中填写以下内容：

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=替换为自己的_DeepSeek_API_Key
LLM_TIMEOUT_SECONDS=30

TELEGRAM_BOT_TOKEN=替换为自己的_Bot_Token
TELEGRAM_REQUEST_TIMEOUT_SECONDS=30
TELEGRAM_POLL_TIMEOUT_SECONDS=20
TELEGRAM_ALLOWED_USER_ID=替换为自己的数字用户_ID

REMINDER_MAX_DELAY_SECONDS=3600
REMINDER_MAX_ACTIVE_TASKS=5

MYCLAW_GATEWAY_HOST=127.0.0.1
MYCLAW_GATEWAY_PORT=18790
MYCLAW_GATEWAY_TOKEN=替换为至少32字符的本机随机Token
```

说明：

- 当前代码只支持 `LLM_PROVIDER=deepseek`。
- `TELEGRAM_REQUEST_TIMEOUT_SECONDS` 必须大于 `TELEGRAM_POLL_TIMEOUT_SECONDS`。
- `TELEGRAM_ALLOWED_USER_ID` 必须是正整数，且只允许该用户从私聊使用 Bot。
- Gateway 只允许监听 `127.0.0.1`，Token 只保存在本机 `.env` 中。
- 模型名称是否可用取决于 DeepSeek 当前服务；如模型服务返回错误，应先检查模型名称、Key 和账户状态。

## 运行终端 Agent

先检查解释器：

```bash
python src/check_environment.py
```

再检查模型配置：

```bash
python src/config.py
```

启动终端 Agent：

```bash
python src/main.py
```

输入 `/exit` 或 `/quit` 退出终端程序。

可以尝试：

```text
请使用计算工具计算 (23 * 17 + 6) / 5，只回答结果。
请使用工具告诉我当前系统时间。
/remember 偏好使用简洁的中文回答
```

## 运行 Gateway 与 CLI

在第一个终端启动 Gateway：

```bash
source .venv/bin/activate
myclaw gateway run
```

保持 Gateway 运行，在第二个终端执行：

```bash
source .venv/bin/activate
myclaw gateway status
myclaw chat "请只回复：Gateway 已连接。"
myclaw sessions list
myclaw logs --limit 20
myclaw remind 10 定时提醒验证
myclaw tasks list
```

CLI 只通过本机 Gateway 请求服务，不会直接读取会话数据库、日志或启动第二个 Agent。`myclaw gateway status` 还会显示 Agent 运行器、状态存储、提醒服务和工具策略的固定诊断状态；诊断不包含 Token、模型 Key、会话正文或任务正文。
`myclaw remind` 会使用已配置 Telegram 白名单用户作为投递目标；必须保持 Gateway 运行，才能创建或投递提醒。

## 配置并运行 Telegram Agent

1. 在 Telegram 中通过 BotFather 创建 Bot，并获得 Bot Token。
2. 在 `.env` 中填写 `TELEGRAM_BOT_TOKEN`。
3. 先向 Bot 发送 `/start`。
4. 在终端运行：

   ```bash
   python src/telegram_setup.py
   ```

5. 从输出中找到自己的数字用户 ID，填入 `.env` 的 `TELEGRAM_ALLOWED_USER_ID`。
6. 在第一个终端启动 Gateway：

   ```bash
   myclaw gateway run
   ```

7. 保持 Gateway 运行，在第二个终端启动 Telegram 渠道：

   ```bash
   python src/telegram_main.py
   ```

8. 在 Telegram 私聊中发送文字消息。

用 `Ctrl+C` 停止 Telegram 渠道。Telegram 中的 `/quit` 和 `/exit` 不会停止服务，只会提示继续聊天。可以发送 `/remind 10 定时提醒验证` 创建提醒；Telegram 只负责把命令转交 Gateway，真正的任务保存、扫描与发送都由 Gateway 完成。

## 运行测试

运行全部自动化测试：

```bash
python -m pytest -q
```

当前预期结果：

```text
76 passed
```

测试覆盖：

- Telegram 白名单配置校验；
- 安全计算拒绝代码形态输入；
- 受限笔记拒绝越界路径；
- 本地会话保存与损坏记录跳过；
- Telegram 私聊白名单规则；
- Gateway 会话消息、会话列表与脱敏日志接口；
- CLI 只能通过 Gateway 请求服务；
- Telegram 到 Gateway 的稳定会话映射与服务不可用处理；
- Gateway 持久提醒的状态机、重启恢复、发送失败和资源上限；
- 提醒任务 API、CLI 与 Telegram 创建入口，以及提醒正文脱敏。
- Gateway 固定诊断字段与 CLI 展示；
- Gateway 工具白名单、模型伪造越权调用的执行阶段拒绝，以及损坏任务状态的安全终结。

测试会使用临时目录和临时环境变量，不读取真实会话、笔记或私密配置。

## 安全边界

- `.env`、日志、会话、长期记忆和受限笔记目录均被 Git 忽略。
- 算术工具只允许数字、括号和基础运算符，不执行 Python 代码。
- 笔记只能读写受限目录中的单个 Markdown 文件，禁止路径穿越和同名覆盖。
- 长期记忆必须由 `/remember` 明确授权，普通聊天内容不会自动保存。
- Telegram 仅允许配置中的单一用户从私聊进入 Agent；群聊和其他用户不会进入模型。
- Telegram 不直接创建 Agent、读取会话或调度提醒；文字消息只能转发到带 Token 的本机 Gateway。
- 提醒任务只允许投递到已配置的单用户 Telegram 私聊；任务列表、日志和 CLI 输出不包含提醒正文或投递地址。
- Telegram API 未提供可用于本项目的幂等投递键，因此系统在发送前记录 `delivering`，异常中断后不自动重发；这是避免重复外部消息的明确取舍，不是严格的“恰好一次”保证。
- Gateway 只向模型提供代码白名单内的工具，并在执行阶段再次拒绝越权调用；当前白名单不含 `write_note`。`save_memory` 即使在白名单内，也只能与用户本轮 `/remember` 的明确授权内容逐字匹配。
- 工具调用次数有限制，未知工具会被拒绝。

如果 Key 或 Token 意外泄露，应立即在对应服务中撤销或重新生成，而不是只删除 Git 历史中的文本。

## 常见问题

### 提示缺少配置

确认已创建 `.env`，并且所有必填项都已填写；不要只修改 `.env.example`。

### 模型请求失败

检查网络、`LLM_API_KEY`、模型名称、账户状态和超时配置。错误提示不会显示 Key，请不要为排查而打印 Key。

### Telegram Agent 无法启动

检查 Bot Token、白名单用户 ID 是否为正整数，以及 Telegram 请求超时是否大于长轮询等待时间。

### Bot 没有回复

确认 Gateway 与 `python src/telegram_main.py` 都在运行，并且消息来自已配置的白名单用户私聊。图片、语音和群聊消息不会进入 Gateway。

### 天气查询失败

天气查询依赖 Open-Meteo 和网络连接；城市不存在、网络异常或服务异常都会得到明确的失败提示。

## 学习记录

开发过程与每步验证记录在：

- `OpenClaw-Agent-学习开发计划.md`
- `learning-log.md`

本机私密的过程复盘不提交到 Git，避免意外暴露环境或个人信息。
