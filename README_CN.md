# task-console

一台 Windows 机器的本地控制台，只监听环回地址：看它的计划任务、某个根目录下的所有 git 仓库，以及你指给它的 skill、记忆和对话目录。

[![本地控制台](https://img.shields.io/badge/%E6%9C%AC%E5%9C%B0-%E6%8E%A7%E5%88%B6%E5%8F%B0-orange?style=flat)](scripts/task_console/server.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![平台](https://img.shields.io/badge/%E5%B9%B3%E5%8F%B0-Windows-green?style=flat)](scripts/task_console/server.py)
[![语言](https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-EN%20%2F%20CN-blue?style=flat)](#语言)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ 先读这里，设计理念

它存在只为诚实地回答一个问题：**有没有什么东西坏了，但现在看起来是好的？** 由此引出三条承诺，它们比任何一块面板都重要。

**「没查」和「零」必须是两种输出。** 每块面板都分开报告两件事：它有没有查成，以及它查出了什么。一个没人配过的来源显示为 NOT CHECKED，绝不画一张空的绿表；自检把它算进自己的分母，所以一次跳过了来源的检查不可能打出满分。

**任何清单都不留第二份。** 页面能执行的动作全在 `maint.py` 的一张封闭表里，那张表自己就是权威。它读的每一个环境变量列在 `scripts/task_console/README.md`，并由一条测试双向对账，因为设置这些变量的启动器住在这个仓之外。第二份删不掉的，就上闸门把两份钉在一起；删得掉的，就删掉。

**闸门必须先被证明会失败，才值得信。** 投毒证明它会红，负对照证明它不是对什么都红。这个仓的测试里记着那些「第一次投毒没红」的具体原因，那才是最值钱的部分。

## 它是什么（不是什么）

`scripts/task_console/server.py` 在 `127.0.0.1` 上服务单页，每次启动现铸一个令牌，且从不落盘。`console_ingest.py` 在带外承担读取 Windows Operational 事件日志的开销，这样页面加载就不必付这笔钱；那份日志是个大约五天的环形缓冲，所以没能在窗口内被摄取的东西是丢了而不是晚了，控制台会明说这件事，而不是给一个已经不动了却看着很笃定的数字。

它**不是** Claude Code 的 skill 或 plugin，也不带 `SKILL.md`：它是一个你跑起来的服务端。它**不是**监控服务，没有轮询，也没有告警。它**不**可移植：它通过 PowerShell 和 `pywin32` 读 Windows 计划任务，`server.py` 在别的平台上会刻意拒绝启动。

页面可以动手，而这条约束的形状不会漂：它**不能创建**任务，因为正确地创建一个任务意味着在好几个地方登记，而一个跳过这些步骤的按钮恰好会制造出那种流程本来要防的未登记任务。它可以退役一个任务，那是反方向的操作，正因为它是减法才适合自动化。凡是会离开这台机器的动作，一律两步，绝不一步。

## 安装

Windows，Python 3.12 或更新。

```bash
git clone --recursive https://github.com/DaizeDong/task-console
cd task-console
pip install pytest pywin32
git config core.hooksPath .githooks    # 装上闸门；本地配置，提交不进去
```

`--recursive` 是要紧的。闸门住在 submodule 里，普通 clone 会留下存在但为空的目录，那是一个没有防护的仓，不是一个干净的仓。已经 clone 过了就跑 `git submodule update --init --recursive`。

## 快速开始

```bash
python scripts/task_console/server.py --port 8787
```

它读的每一个路径都来自一个 `TASK_CONSOLE_*` 环境变量。这个工具**不在自己的命名空间之外出厂任何默认值**，所以一个没配过的控制台照样启动、照样服务，然后告诉你它什么都没查。

## 细节各自住在哪

每份清单只有一个家，而且不是这个文件。手上的问题是哪一个，就顺着哪一条指针走。

| 读这个 | 当你要问 |
| --- | --- |
| [scripts/task_console/README.md](scripts/task_console/README.md) | 页面上有什么，以及它读的每一个环境变量 |
| `scripts/task_console/maint.py` | 页面能对这台机器执行哪些动作 |
| `scripts/task_console/server.py` | 有哪些路由，以及让写入端点得以存在的那三道控制 |
| [docs/changing-this.md](docs/changing-this.md) | 一次改动不能破什么，以及这个仓已经踩过的坑 |
| [docs/cleanup-plan.md](docs/cleanup-plan.md) | 一次全仓审查查出了什么，其中执行了多少 |

## 真实数据住在哪

这个仓里不存任何真实数据。控制台的数据库和它的持久导出住在一个私有伴生仓，运行时由 `guards/tools/datadir.py` 这个共享解析器解出来。解析不到伴生仓时，控制台报 UNINITIALISED 并给出初始化指引；它绝不会退回到往这个仓里写，因为仓内 fallback 不是便利，它就是泄漏。

## 测试

```bash
python -m pytest tests/ -q
```

测试面向 Windows，因为被测的东西就是 Windows。CI 在 `windows-latest` 上跑同一套，并在收集到的条数掉到某个绿色运行实测出来的下限以下时判失败。

## 局限

只管一台机器，就是它自己跑着的那台。只支持 Windows。没有轮询也没有告警，所以控制台报的是你打开它那一刻为真的东西。它推导运行历史所依据的事件日志只存大约五天，所以窗口之外的历史只活在持久导出里，而且只覆盖开始摄取之后的那段时间。

## 语言

English (`README.md`) · 中文 (`README_CN.md`)

## Roadmap · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE)（MIT）。

与仓库统一规范的每一处偏离及其理由，记在 [docs/2026-09-22-spec-adaptation.md](docs/2026-09-22-spec-adaptation.md)。
