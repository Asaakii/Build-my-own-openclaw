# Build My Own OpenClaw

从零学习开发的个人 Agent：调用现成大模型，具备会话记忆、受控工具、Skills，并可在终端和 Telegram 私聊中工作。

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
- 通过 `/remind 秒数 内容` 创建短时 Telegram 提醒。
- 使用 pytest 覆盖关键安全边界；当前共有 12 项自动化测试。

## 当前限制

- 仅支持一个 Telegram 白名单用户的私聊文字消息。
- 当前会话存储是单用户设计，不支持多用户会话隔离或并行处理。
- Telegram 渠道使用单进程长轮询；程序停止后，未触发的提醒会被取消。
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

安装项目依赖：

```bash
python -m pip install -r requirements.txt
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
```

说明：

- 当前代码只支持 `LLM_PROVIDER=deepseek`。
- `TELEGRAM_REQUEST_TIMEOUT_SECONDS` 必须大于 `TELEGRAM_POLL_TIMEOUT_SECONDS`。
- `TELEGRAM_ALLOWED_USER_ID` 必须是正整数，且只允许该用户从私聊使用 Bot。
- 提醒最长等待时间和同时运行数量均受配置限制。
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

## 配置并运行 Telegram Agent

1. 在 Telegram 中通过 BotFather 创建 Bot，并获得 Bot Token。
2. 在 `.env` 中填写 `TELEGRAM_BOT_TOKEN`。
3. 先向 Bot 发送 `/start`。
4. 在终端运行：

   ```bash
   python src/telegram_setup.py
   ```

5. 从输出中找到自己的数字用户 ID，填入 `.env` 的 `TELEGRAM_ALLOWED_USER_ID`。
6. 启动 Telegram Agent：

   ```bash
   python src/telegram_main.py
   ```

7. 在 Telegram 私聊中发送文字消息。

用 `Ctrl+C` 停止 Telegram Agent。Telegram 中的 `/quit` 和 `/exit` 不会停止服务，只会提示继续聊天。

创建短时提醒：

```text
/remind 10 十秒后的提醒内容
```

提醒不会调用模型，也不会写入主聊天会话；程序停止后，未触发的提醒会被取消。

## 运行测试

运行全部自动化测试：

```bash
python -m pytest -q
```

当前预期结果：

```text
12 passed
```

测试覆盖：

- Telegram 白名单配置校验；
- 安全计算拒绝代码形态输入；
- 受限笔记拒绝越界路径；
- 本地会话保存与损坏记录跳过；
- Telegram 私聊白名单规则；
- 提醒命令解析与无效参数拒绝。

测试会使用临时目录和临时环境变量，不读取真实会话、笔记或私密配置。

## 安全边界

- `.env`、日志、会话、长期记忆和受限笔记目录均被 Git 忽略。
- 算术工具只允许数字、括号和基础运算符，不执行 Python 代码。
- 笔记只能读写受限目录中的单个 Markdown 文件，禁止路径穿越和同名覆盖。
- 长期记忆必须由 `/remember` 明确授权，普通聊天内容不会自动保存。
- Telegram 仅允许配置中的单一用户从私聊进入 Agent；群聊和其他用户不会进入模型。
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

确认运行的是 `python src/telegram_main.py`，并且消息来自已配置的白名单用户私聊。图片、语音和群聊消息不会进入 Agent。

### 天气查询失败

天气查询依赖 Open-Meteo 和网络连接；城市不存在、网络异常或服务异常都会得到明确的失败提示。

## 学习记录

开发过程与每步验证记录在：

- `OpenClaw-Agent-学习开发计划.md`
- `learning-log.md`

本机私密的过程复盘不提交到 Git，避免意外暴露环境或个人信息。