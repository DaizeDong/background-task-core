# task-console

用于单台 Windows 机器的本地控制台，仅监听环回地址，展示计划任务、指定根目录下的 Git 仓库，以及配置的技能、记忆和对话目录。

控制台汇总 agent 工作、记录的结果、待办承诺和本机自动化。工作记录由现有 reminder 属主提供；Windows 计划任务提供触发器，task-console 保留声明、登记记录和受控操作。

工作台区分人工决策与技术诊断。每个来源报告检查覆盖范围：来源不可用时不会显示成检查成功的空列表，完成摘要也不能替代执行证据。

[![本地控制台](https://img.shields.io/badge/%E6%9C%AC%E5%9C%B0-%E6%8E%A7%E5%88%B6%E5%8F%B0-orange?style=flat)](scripts/task_console/server.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![平台](https://img.shields.io/badge/%E5%B9%B3%E5%8F%B0-Windows-green?style=flat)](scripts/task_console/server.py)
[![语言](https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-EN%20%2F%20CN-blue?style=flat)](#语言)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ 先读这里，设计理念

控制台帮助识别检查缺失、状态异常和缺乏执行证据的情况。

**检查状态与检查结果分别报告。** 每块面板区分未检查、测量值为零和不可用或错误。未配置的来源显示 NOT CHECKED，并计入自检分母，跳过检查不能获得完整通过结论。

**每项控制有明确权威。** `maint.py` 的封闭动作表定义页面可执行的维护操作。`scripts/task_console/README.md` 列出环境变量，测试双向核对文档与代码；仓外启动器依赖这份契约。无法消除的重复表示必须有一致性检查。

**检查器需要失败样本和负对照。** 注入故障验证检查能够拒绝错误状态，负对照验证合法状态能够通过。测试保留曾漏检的故障条件，防止同类回归。

## 它是什么（不是什么）

`scripts/task_console/server.py` 在 `127.0.0.1` 提供单页服务，每次启动生成新令牌，令牌从不落盘。`console_ingest.py` 独立读取 Windows Operational 事件日志，页面加载不执行摄取。

主体是本地服务端，附带用于声明、审核和登记的 [automation-management skill](skills/automation-management/SKILL.md)。服务通过 PowerShell 和 `pywin32` 读取 Windows 计划任务，在其他平台上拒绝启动。采集时效与历史覆盖限制见下文“局限”。

## 页面能做什么

页面的维护动作定义在 `maint.py`。会话操作使用独立的鉴权路由，并由 [convo-chain](https://github.com/DaizeDong/convo-chain) 提供转录解析、导出、分叉、重命名、移动和确认删除。控制台负责路由、请求检查与界面，库负责转录规则和文件事务。会话的中文名称建议由 `llmcall` 生成，只填进改名框，保存仍是一次普通的改名。操作范围与限制见[控制台 README](scripts/task_console/README.md)。

任务的创建和迁移走[任务登记](docs/task-registration.md)里描述的声明、审核与登记接口。网页动作表不提供一条不受约束的计划任务捷径。登记在改动任务定义之前先记下授权与恢复证据；对外动作仍保留原有的审核步骤。

导航把信息分成工作、自动化和资源三组。工作、结果和活动视图复用同一批属主记录。流水线和执行细节归在自动化下面；对话和模型调用为工作记录提供佐证。技术异常留在诊断里，不会变成需要人批准的事项。数据归属和接入边界见[工作平台设计](docs/work-platform.md)。

流水线证据只说它能说的：一张零差异的同步回执不能证明能力对齐，一份新鲜的备份产物也不能证明每一步都跑了。界面不新增调度器、状态存储、通知通道或 agent 执行器。已有的任务控制授权和对外动作审核照常生效。

对外动作保留两步审核流程。

## 安装

Windows，Python 3.12 或更新。

```bash
git clone --recursive https://github.com/DaizeDong/task-console
cd task-console
pip install pytest pywin32 PyYAML
git config core.hooksPath .githooks    # 装上闸门；本地配置，提交不进去
```

控制台还在模块顶层导入三个钉了版本的库：`convo-chain`、`fleet-guards` 和 `llmcall`（见 `pyproject.toml`）。`llmcall` 是私有仓，公开 clone 装不上它；CI 具体装哪个版本、怎么读这个私有仓，只在 `.github/workflows/tests.yml` 里写一份。

`--recursive` 是要紧的。闸门住在 submodule 里，普通 clone 会留下存在但为空的目录，那是一个没有防护的仓，不是一个干净的仓。已经 clone 过了就跑 `git submodule update --init --recursive`。

## 快速开始

```bash
python scripts/task_console/server.py --port 8787
```

资源路径由环境变量选择，默认值和未配置状态见[环境变量表](scripts/task_console/README.md)。llmcall 资源和可执行文件保留各自已有的默认位置。未配置的来源会明确显示为未检查。

## 配置

将 `TASK_CONSOLE_CONFIG` 指向 PRIVATE 伴生仓。切换配置时，在启动进程中加载另一个伴生仓的设置，并重新核对显式资源覆盖值。[CONFIG.md](CONFIG.md) 说明完整发现顺序、环境变量、初始化和只读检查。`tools/init_config.py` 在已初始化的 PRIVATE 伴生仓生成 `settings.ps1`，已有文件保持原样。`tools/verify_config.py --json` 检查本地服务端和历史数据库的前提条件，不启动摄取，也不替可选动作作就绪证明。数据库覆盖路径仍须通过来源契约、PRIVATE 和版本管理检查。

## 细节各自住在哪

运行接口、配置和维护约束由以下参考文档定义。

| 读这个 | 当你要问 |
| --- | --- |
| [scripts/task_console/README.md](scripts/task_console/README.md) | 页面上有什么，以及它读的每一个环境变量 |
| `scripts/task_console/maint.py` | 页面能对这台机器执行哪些动作 |
| `scripts/task_console/server.py` | 有哪些路由，以及让写入端点得以存在的那三道控制 |
| [docs/changing-this.md](docs/changing-this.md) | 一次改动不能破什么，以及这个仓已经踩过的坑 |
| [docs/cleanup-plan.md](docs/cleanup-plan.md) | 一次全仓审查查出了什么，其中执行了多少 |

<a id="只读声明生成与保留规则"></a>

## 只读声明生成

`task_console.compiler.plan(request)` 和 `task-console plan` 根据组件 `.console.json` 声明、显式私有绑定、机器覆盖项及调用方提供的旧版快照生成字段差异和文件内容，不写文件、不查询或启动计划任务。现有任务定义在审核通过的接管或迁移事务转移责任之前仍有权威，生成方案本身不会转移权威。

安装到独立运行环境后，入口为 `python -m task_console plan`；源码入口为 `python -m scripts.task_console plan`。原有 `python scripts/task_console/server.py` 入口保留。完整合成示例、输入契约和一致性限制见[控制台说明](scripts/task_console/README.md)。真实请求和方案保存在私有仓；公开示例由 `python tools/make_fixtures.py` 生成。

## 真实数据住在哪

控制台数据库和持久导出保存在私有伴生仓，运行时由 `guards/tools/datadir.py` 解析。解析不到伴生仓时报告 UNINITIALISED 并提供初始化指引；禁止写回工具仓。

[存储说明](docs/storage.md)和[来源契约](storage.contract.json)区分当前运行状态、恢复证据与已完成的开发产物。预算用于触发审核，不允许为达标而截断核心历史。

## 测试

```bash
python -m pytest tests/ -q
```

测试针对 Windows 平台。CI 在 `windows-latest` 上运行同一套测试，收集数量低于已通过运行中观察到的下限时会失败。

## 局限

控制台只报告所在 Windows 机器的状态，不轮询，也不发送告警。运行历史来自环形事件日志，其保留窗口取决于配置容量和事件量。窗口内未被摄取的事件无法在之后补回；窗口外的历史仅限于摄取开始后已保存在持久导出中的记录。页面报告来源与摄取时效，不能据此假定历史完整。

## 语言

English (`README.md`) · 中文 (`README_CN.md`)

## Roadmap · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE)（MIT）。

与仓库统一规范的每一处偏离及其理由，记在 [docs/2026-09-22-spec-adaptation.md](docs/2026-09-22-spec-adaptation.md)。
