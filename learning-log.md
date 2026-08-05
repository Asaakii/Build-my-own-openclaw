# 学习日志

## 2026-03-05：项目初始化

- 完成了本地 Git 仓库初始化，并关联到 GitHub 远程仓库。
- 理解了 Git 负责记录项目历史，GitHub 是远程备份与协作平台。
- 建立了 src、tests、workspace/notes 等目录。
- 当前疑问：Python 虚拟环境是什么，为什么项目需要它？

## 2026-03-05：Python 开发环境

- 用 Python 3.12.13 创建了项目专属的 .venv 虚拟环境。
- 激活后，python 指向项目目录中的 .venv/bin/python。
- 退出后，python3 回到系统全局的 Python 3.14。
- 安装并验证了 pytest 9.1.1。
- 理解了虚拟环境用于隔离不同项目的依赖。

## 2026-03-05：安全配置加载

- 安装了 python-dotenv，用于从本机 .env 文件读取配置。
- 创建了公开的 .env.example 和不会提交的本机 .env。
- 实现了模型供应商、模型名称与 API Key 的配置校验。
- 验证了 API Key 不会被打印到终端。
- 验证了缺失配置时会显示明确错误。