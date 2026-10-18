# task-console

单台 Windows 机器的本地控制台，只监听环回地址，管这台机器的计划任务、它们的运行历史和健康状况，以及这些任务所服务的工作记录（待办和 agent 工作）。

Windows 计划任务负责触发；task-console 保管任务声明、登记权威和受控操作。每个来源都说明自己查了多少：来源读不到不会显示成一张空的成功列表，完成摘要也不能顶替执行证据。

[![本地控制台](https://img.shields.io/badge/%E6%9C%AC%E5%9C%B0-%E6%8E%A7%E5%88%B6%E5%8F%B0-orange?style=flat)](scripts/task_console/server.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![平台](https://img.shields.io/badge/%E5%B9%B3%E5%8F%B0-Windows-green?style=flat)](scripts/task_console/server.py)
[![语言](https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-EN%20%2F%20CN-blue?style=flat)](#语言)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## 设计

**查没查和查出了什么分开说。** 每一屏都区分未检查、读到零和读不到。没配置的来源显示「未检查」，跳过的检查不会被当成通过。

**每个控制只有一个权威。** 任务开关、删除和登记都经 `lifecycle.py` 与 `registry.py` 里的登记权威，页面上没有直通计划程序的捷径。

## 模块

`scripts/task_console/` 一共八个模块：

| 模块 | 负责 |
| --- | --- |
| `registry.py` | 声明契约、备份白名单解析、组件编译、计划编译、登记事务、创建前检查 |
| `scheduler.py` | 任务名规则、计划程序适配、任务 XML 的生成与规范化、COM 与 DPAPI 传输 |
| `store.py` | 权威存储：文件资源、日志、受保护保管库、权威历史、运行时 |
| `lifecycle.py` | 控制动作、接管、对账、导出与恢复、删除任务、修复工单、被动观测 |
| `runs.py` | 退出码归一、健康判定、新鲜度、健康日志、事件日志读取、控制台数据库与摄入、组件状态读取 |
| `integrations.py` | 读取隔离、共享工作服务（schedule-reminder）及其动作、关联工作项、接入登记表 |
| `server.py` + `console.html` + `static/` | HTTP 服务和页面 |
| `__main__.py` | 命令行 |

## 页面

五个分区：工作台、工作记录、自动化（任务开关、同步与备份、运行详情）、接入（控制台读的来源）、诊断（失败的任务、过期的输出、摄入状态和文件更新灯板）。任务行可以启用、停用、运行、停止、开修复工单和删除；删除先出预览，受控制器管理的任务经权威退役。见[任务控制](docs/task-control.md)和[工作平台](docs/work-platform.md)。

## 安装

Windows，Python 3.11 或更新。

```bash
git clone https://github.com/DaizeDong/task-console
cd task-console
pip install -e . -e "$(git config --get fleet.guards)"
git config core.hooksPath .githooks
```

依赖见 `pyproject.toml`：`fleet-guards`、`llmcall`、`pywin32`。

## 运行

```bash
python -m task_console.server --port 8787            # 页面,只绑 127.0.0.1
python -m task_console ingest [--skip-runlog]       # 写控制台数据库
python -m task_console status --configured          # 组件状态读取
python -m task_console observations --periodic ...  # 一次被动健康观测
python -m task_console <权威命令> ...                # plan、registration-plan、creation-check、apply、retire、control 等
```

服务每次启动生成新令牌，从不落盘。摄入在页面之外跑，打开页面不会去读 Windows 事件日志。

## 配置

全部是环境变量，由机主的启动器从私有伴生仓的 `data/launcher-binding.json`（[合成示例](launcher-binding.json.example)）设置。[CONFIG.md](CONFIG.md) 列出每个变量以及不设时的表现。

## 只读的声明生成

`task_console.registry.plan(request)` 和 `python -m task_console plan` 把组件 `.console.json` 声明、私有绑定、机器覆盖和旧快照编译成字段对照和生成内容，不写文件，也不碰计划程序。

```text
python -m task_console plan --component examples/console/.console.json --bindings examples/console/bindings.example.json --machine examples/console/machine.console.example.json --baseline examples/console/baseline.example.json
```

真实请求和返回的计划含机器数据，只能放私有存储。公开示例用 `python tools/make_fixtures.py` 重新生成。

## 数据在哪

数据库和运行事件导出放在 `fleet_guards.runtime` 解析出的私有伴生仓。数据库 `data/task-console/console.sqlite3` 是那里的本地状态，不进版本管理；进版本管理的历史是按月的文本导出 `data/task-console/run-events/YYYY-MM.jsonl`。解析不到伴生仓时控制台报「未初始化」，写入永远不会落回本仓。

## 局限

只报告运行它的这台 Windows 机器，不发告警。运行历史来自循环事件日志，摄入之前滚掉的事件就没了。按月导出不含原始返回码、任务设置历史和摄入记录，不能当完整的数据库恢复。

## 语言

English (`README.md`) · 中文 (`README_CN.md`)

## 路线图 · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE)（MIT）。改动之前先读 [docs/changing-this.md](docs/changing-this.md)。与仓库规范的偏离及理由记在 [docs/2026-09-22-spec-adaptation.md](docs/2026-09-22-spec-adaptation.md)。
