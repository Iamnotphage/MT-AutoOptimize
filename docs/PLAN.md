---
name: mt3000-agentic
overview: 基于 Python 的 LangChain/LangGraph（agentic 编排 + 工具调用），做一个面向 MT-3000 的代码优化 CLI：先生成可应用补丁并保证登陆节点编译通过，再通过 SSH+Slurm 上传、运行基准与正确性校验，最终回读性能/正确性结果并迭代 AM 向量化与 SM 缓存优化。
todos:
  - id: mt3000-api-contract
    content: 定义 CLI 子命令、任务配置文件、统一输入输出与每轮结果 JSON 结构
    status: pending
  - id: mt3000-tooling
    content: 实现 tools：登陆节点编译/验证、diff/apply 补丁、SSH 同步/提交/轮询/拉取 Slurm 结果
    status: pending
  - id: mt3000-agent-stages
    content: 实现两个优化阶段 agent（AM 向量化、SM 缓存）：生成补丁 + 明确验证步骤与失败风险
    status: pending
  - id: mt3000-pipeline-runloop
    content: 把 agent + tools 组合成优化闭环：登陆节点编译通过 -> 同步上传 -> slurm 跑 -> 校验正确性与耗时 -> 迭代
    status: pending
  - id: mt3000-e2e
    content: 端到端联调：先用最小登陆节点编译+slurm 模板跑通，再开启 AM/SM 迭代；记录每轮结果并做回归
    status: pending
  - id: todo-1773999649136-3reizw2bq
    content: ""
    status: pending
isProject: false
---

## 总体路线

1. 技术栈选择：

- Python 为主工程：用 LangChain 负责 LLM 交互与结构化输出（openai-compatible 多模型接入），用 LangGraph 描述 agent 状态机/循环与工具调用编排。
- 工具执行（文件 diff/apply、SSH 同步、登陆节点编译、slurm 运行、轮询回读）由 Python 实现为“可调用工具”，让 LangGraph 的节点调用它们。

2. Agentic CLI 的核心模块（对齐 LangGraph 的抽象）：

- 工具系统（LangChain Tools / structured function calling）：把“读文件、diff、应用补丁、登陆节点编译、SSH 同步、提交 slurm、轮询结果、拉取日志、解析性能/正确性”等封装为 tools，并为每个 tool 明确定义输入输出结构（建议 Pydantic schema）。
- agent 状态机（LangGraph StateMachine）：把整个优化闭环拆成节点（生成候选补丁 -> 同步到登陆节点 -> 登陆节点编译 -> slurm 运行 -> 回读解析 -> 评估 -> 迭代/终止），让模型在每轮只负责“下一步决策/生成候选”，真正的副作用由工具执行层完成。
- 越权控制（可选 Plan/Approval 流）：如果你希望“先生成计划/补丁，再确认后 apply”，可在 LangGraph 中加入显式的 approval 节点（例如要求人工确认或写入 plan 文件而不直接 patch）。

3. MT-3000 远程执行链路（你选择 SSH + Slurm）：

- 本地阶段：LLM 生成候选补丁（以及需要的变更清单）-> 通过 SSH 把代码/补丁同步到登陆节点 -> 运行登陆节点编译（至少“编译通过” + 可选 unit test/静态检查）-> 产出结构化候选（补丁/修改点/预期收益）。
- 远程阶段：
- `ssh/scp`：把补丁应用后的源代码/差分打包上传到集群工作目录。
- 提交 `sbatch`：运行你提供的 Slurm 模板（benchmark + correctness checks + perf timing）。
- 轮询：`squeue`/`sacct`/读取作业输出文件。
- 拉取结果：下载 stdout/stderr、性能时间、校验输出。
- 回写结果：把“正确性是否通过 + time 是否下降 + 是否触发失败模式（编译失败/运行失败/数值偏差）”结构化记录，再驱动下一轮迭代。

## 你要做的工作拆解（按里程碑）

### 里程碑 1：确定 CLI 接口 + 输入/输出契约

- CLI 子命令建议：
- `mt3000-opt init`：创建一次优化任务目录（保存模板、配置、初始参数）。
- `mt3000-opt optimize --strategy am,sm`：执行多轮优化（默认先 AM 再 SM，或按你策略）。
- `mt3000-opt run-slurm`：单独跑一次（便于调试模板与 SSH 通路）。
- `mt3000-opt results`：汇总所有候选的正确性/耗时/补丁统计。
- 在任务目录中定义统一 JSON/YAML 配置：
- 登陆节点编译命令、可执行 benchmark 命令
- 正确性检查方式（例如输出对比阈值、或是否存在某些断言/exit code）
- MT-3000 的 SSH 主机、目标路径、slurm partition/资源参数
- 基准脚本模板（sbatch content 或模板参数）

### 里程碑 2：封装 tools（让模型能“安全地做事”）

在你的新 CLI 包中实现一组 tools：

- 文件/补丁类 tools：
- `read_file(path)`、`read_many_files(pattern)`、`write_plan(md)`（计划模式下写 md）
- `create_patch(diff_spec)` 或 `apply_patch(patch)`（只在批准后执行）
- `diff_summary(original, modified)`（输出变更统计，便于模型自检）
- 构建/验证类 tools：
- `ssh_compile(build_cmd, remote_dir)` 或 `run_login_compile(build_cmd)`：确保“登陆节点编译通过”
- `ssh_test(test_cmd, remote_dir)`：可选，但建议至少做最小 sanity check（如果登陆节点环境可用）
- 远程执行类 tools（你已选 SSH）：
- `ssh_upload(local_tar, remote_dir)`：用于把候选代码/补丁同步到登陆节点或工作目录
- `ssh_run(cmd)`（用于 `sbatch`/`squeue`/`sacct`/拉取日志/登陆节点编译命令执行）
- `slurm_submit(script)`：返回 job id
- `slurm_wait(job_id, timeout)`：轮询直至完成
- `slurm_fetch_artifacts(job_id)`：下载输出、提取性能与正确性结果

说明：这些 tools 的“参数 schema + 返回结构”要足够结构化，才能让 agent loop 稳定收敛。

关键复用点：LangGraph 节点 -> 调用工具 -> 把结构化结果写回 State（包含编译/正确性/性能指标），驱动下一轮迭代。

### 里程碑 3：实现优化 agent（AM 向量化 / SM 缓存）

- 把优化拆成两阶段（更容易控风险）：
- Stage A（AM 向量化优化 agent）：目标是更高 SIMD 利用率/减少分支/改善对齐与循环结构。
- Stage B（SM 缓存优化 agent）：目标是数据局部性、blocking/tiling、缓存友好布局。
- 每阶段的 agent 输出必须满足：
- 生成可应用补丁（或明确的“要改哪些函数/哪些循环” + 具体 diff）
- 列出预计收益点与可能破坏点（比如数值误差来源、未对齐风险）
- 给出“回归验证计划”（登陆节点编译+正确性+基准）
- 用 LangGraph 的状态机驱动：模型 -> tools -> 结果 -> 迭代/终止。
- 若你希望“只先写计划再让你确认”，就启用 plan/approval flow（在 LangGraph 中加入 approval 节点或限制 apply tool）。

### 里程碑 4：端到端验证闭环（你要求的验证方式）

按照你说的验证：

1. 登陆节点编译通过（失败就不要上集群）
2. 用 slurm 脚本上传到 MT-3000 跑
3. 校验正确性（数值/输出）
4. 比对执行时间是否优化
5. 把结果结构化存档，并驱动下一轮

输出“每轮”的最小结构化记录（JSON）建议包含：

- 轮次 id、策略（AM/SM）、补丁摘要、编译结果、slurm job id
- 正确性结果（通过/失败 + 误差/日志片段）
- 性能指标（wall time 或你 benchmark 的主指标）

### 里程碑 5：把“agentic”做成可持续迭代的系统

- 支持候选管理：同一轮可保留 N 个候选（top-K）而不是只保留一个。
- 加入失败模式分类：
- 编译失败（编译器错误类型）
- 运行失败（segfault/timeout）
- 正确性失败（数值偏差、边界 case）
- 性能没有提升（丢弃/调整策略权重）
- 引入“提示词/约束更新”：用失败信息回灌到后续轮次的系统提示或约束文本。

## 技术栈建议（回答你的疑惑）

- 推荐：用 LangChain/LangGraph 做 agent 编排与多模型接入（尽量统一 openai-compatible 形式，便于支持“很多不同的 LLM”）。
- 工具执行尽量保持“无状态/可重现”：把 SSH/编译/解析都做成确定性的 tools，并把执行证据（命令、返回码、日志摘要、解析结果）结构化回灌 State，减少模型反复试错成本。

## 计划落到代码上的关键点（你会改/加哪些类型文件）

- 新增一个 CLI 包（建议在当前 monorepo 下新增 `packages/mt3000-cli` 或类似目录），使用 Python 实现，包含：
- `src/index.py`（或 `src/cli.py`）：CLI 入口（例如 Typer/Click）+ 命令路由
- `src/agents/`：LangGraph 节点/策略（AM/SM 阶段提示词、输出 schema、终止条件）
- `src/tools/`：tools 实现（SSH 同步、登陆节点编译、slurm 提交/轮询/回读、patch/diff 处理）
- `src/pipeline/`：State 定义、LangGraph 编排、每轮候选管理与评估逻辑（正确性/性能解析、fail mode 分类、迭代策略）
- `src/llm/`：LLM 适配层（openai-compatible，多模型切换、统一结构化输出）