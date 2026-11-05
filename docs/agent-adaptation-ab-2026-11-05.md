# Agent 适配：精简必读契约的 A/B 测量（2026-11-05）

范围：在不增加功能的前提下，让当前 agent 接入工作台后不变差。本文记录改了什么、怎么测、测到了什么，以及测量中暴露出的、本次**未修**的两个产品缺陷。

## 结论

- **必读量从约 26k token 降到约 3.5k token**，任务完成率不变（每组 3 次，见下表），回答中关于可信度的说明质量相同。
- **`main` 上 Claude Code 实际读不到 `cae` 契约。** 旧 `cae` guide 有 68.8k 字符，超过 Claude Code 默认的 25k token MCP 输出上限：agent 收到的是 `Error: result … exceeds maximum allowed tokens` 和一个文件路径，服务端却把它记为“已读”并放行了全部 CAE 工具。本次改动后两个契约各约 1k token，完整到达。
- **测量暴露了一个比上下文更严重的适配问题：写包的工具被标成只读**（见“发现 1”）。当前 agent 会并行发出工具调用，6 次运行全部出现了同一项目上的并行写，其中 1 次把包写坏（`BadZipFile`）。**已修复**，修复后的 3 次复测没有再出现包损坏或绑定失败。

## 改了什么

| 项 | 之前 | 之后 |
|---|---|---|
| `aieng.guide {topic:"cad"}`（CAD 工具前必读） | 30.8k 字符，约 7.7k token | **CAD contract**，约 3.9k 字符 |
| `aieng.guide {topic:"cae"}`（CAE/opt/postprocess 前必读） | 66.6k 字符，约 16.7k token | **CAE contract**，约 4.5k 字符 |
| quickstart（`aieng.agent_readme`） | 约 1.8k token | 约 1.4k token |
| 旧的长章节 | 即 `cad` / `cae` | 原样保留为 `cad-reference` / `cae-reference`，由 `see_also` 指向 |
| `full`（整份 AGENTS.md，约 46k token） | 一次返回，在 Claude Code 中被截断 | 按 `part` 分页（每页 ≤60k 字符，共 4 页），第 1 页包含两份契约，只有第 1 页解锁全部类别 |

契约只写 agent **必须自己做到**的规则和响应字段的读法；代码已强制执行的守卫（`stale_mesh`、`stale_deck`、`run_id_conflict` 等）在拒绝时会自带原因和下一步，它们背后的事故史移到 reference。新增 `test_required_reading_stays_small`，防止必读量重新膨胀。

## 方法

- 任务（`scripts/bench/agent_ab/prompt.md`）：建支架 → Al6061-T6 静力分析（螺栓孔固定、肋顶 500 N 向下）→ 底板 8→12 mm → 重新分析 → 报告前后应力、位移、变化率和可信度。
- 每组 3 次 headless Claude Code（`claude-sonnet-5-5`），基线跑 `main`（42ba28c）的独立 worktree，改动组跑打了补丁的独立 worktree；每次运行有独立的数据目录。
- 评分不看 agent 怎么说，而是事后用 `cae.compare_runs` 从两次运行各自的 FRD 重新读数。比较的是体现 8→12 mm 修改的那一对运行：从每次运行自己的求解输入中测出底板厚度（底板外缘节点的 z 范围）和肋高，要求分别为 8 mm 和 12 mm，肋高都是 25 mm。成功要同时满足以下条件：项目名与要求一致；两次求解的几何不同；绑定数量不变；两次求解输入中记录的载荷都是 −Z 方向 500 N，材料和约束完全相同；两项指标都下降。没有生成包的项目记为任务失败。全部 9 次运行都满足这些条件。评分脚本自身出错的运行记为无效试验，不计入成功率的分母。
- 限制（如实说明）：
  - 只开放 aieng MCP 工具，文件和 shell 工具被禁用，模拟纯 MCP 客户端（如 Claude Desktop）。在能读文件的 Claude Code 里，基线 agent 可以去读被截断后另存的文件，所以“读不到 cae 契约”这一结论只对无文件工具的客户端完全成立。
  - 用户全局 CLAUDE.md 在两组中都被加载（`--bare` 需要 API key，本机只有 OAuth），两组条件相同。
  - prompt 预先批准了建模计划和求解，并禁用了 `AskUserQuestion`：headless 没人回答。
  - 每组 3 次，样本小；成本和 token 的组间差异在噪声范围内，不能据此说改动“更省”或“更贵”。

## 结果

| run | success | stress_pct | disp_pct | cost_usd | input_tok_M | output_tok_k | turns | tool_calls | guide_chars | errors | wall_s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| after-1 | True | -39.68 | -51.5 | 0.629 | 1.92 | 6.8 | 26 | 25 | 10674 | {'cae_generate_mesh:tool_exception': 1, 'cae_generate_solver_input:unbound_setup_faces': 1} | 235.9 |
| after-2 | True | -40.54 | -51.55 | 0.328 | 0.94 | 4.3 | 19 | 18 | 10674 | {} | 194.1 |
| after-3 | True | -39.26 | -51.52 | 0.66 | 1.93 | 7.2 | 27 | 26 | 10674 | {'cae_generate_solver_input:unbound_setup_faces': 1} | 230.2 |
| baseline-1 | True | -41.92 | -51.59 | 0.436 | 1.06 | 5.0 | 20 | 19 | 34018 | {} | 322.9 |
| baseline-2 | True | -44.77 | -51.62 | 0.883 | 3.1 | 9.7 | 34 | 33 | 34018 | {'cae_setup_static:missing_material': 1, 'cae_generate_solver_input:unbound_setup_faces': 1, 'cae_apply_setup_patch:unsupported_operation': 1} | 370.2 |
| baseline-3 | True | -41.87 | -51.59 | 0.664 | 2.12 | 7.1 | 30 | 29 | 34018 | {} | 2476.0 |

`guide_chars` 是 agent 实际收到的两次 guide 响应的字符数之和。基线的 34k 是 `cad` 的 32.6k 加上 `cae` 被截断后返回的 1.4k 错误信息；改动组的 10.7k 是两份契约的完整内容。

| 组 | 成功 | 中位成本 | 中位输入 token（含缓存） | 中位工具调用 |
|---|---|---|---|---|
| 基线（`main`） | 3/3 | $0.66 | 2.12M | 29 |
| 改动 | 3/3 | $0.63 | 1.92M | 25 |

- **评分取的是体现 8→12 mm 修改的那一对运行。** 评分脚本从每次运行自己的求解输入测出底板厚度和肋高，选底板分别为 8 mm 和 12 mm、肋高都是 25 mm 的一对，若有多对则取运行顺序上最接近的一对。baseline-3 先在 1.5 mm 网格上求解，后来在 2 mm 网格上先算 12 mm（`run_003`）、再算 8 mm（`run_004`），共 4 次运行，所以比较的是 `run_004` 和 `run_003`：-41.87% / -51.59%，与 agent 自己的报告一致。最初的评分脚本比较的是最早和最晚两次运行，对这次运行比到的是两次 8 mm 解，这个问题已修正，全部 9 次运行都已按新规则重新评分。
- **两组的成本、token 和调用次数差异都小于组内差异**（基线自身从 $0.44 到 $0.88），3 次的样本不足以说明改动更省。能确定的只有两点：必读内容变小了，并且完整到达了 agent。

可信度说明（关键词检查，两组完全一致）：所有运行都提到了网格精度、未做收敛性研究、节点平均峰值和线性分析的局限；没有一次运行点名 `credibility` 等级。

## 发现 1（已修复）：写包的工具被标为只读

`runtime.register_tool` 的默认值是 `read_only = not requires_approval`，所以凡是不需要审批但会写包的工具都被标成只读。受影响的包括 `cae.setup_static`、`cae.generate_mesh`、`cae.generate_solver_input`、`cae.apply_setup_patch`、`cae.apply_load_case`、`cae.extract_solver_results`、`cae.extract_field_regions`、`cae.map_results`、`opt.run_topology_optimization`、`opt.writeback_to_shape_ir`（会替换几何）、`postprocess.*`、`aieng.convert`、`aieng.create_project` 等 20 多个。后果：

1. **它们不进项目写锁。** AGENTS.md 承诺“同一项目的写操作会被串行化”，对这些工具并不成立。
2. **它们向客户端声明 `readOnlyHint: true`。** Claude Code 把只读工具当作可以并发执行的工具。6 次运行全部在同一消息里并行发出了同一项目的写调用，例如 `cad.edit_parameter` 与 `cae.generate_mesh`（baseline-3）、`cae.setup_static` 与 `cae.generate_mesh`（after-1、after-3）。after-1 中网格生成读到写了一半的包：`BadZipFile: Bad CRC-32 for file 'geometry/preview.glb'`。
3. **只读模式（`AIENG_MCP_BLOCK_APPROVAL_TOOLS=1`）拦不住它们。**

**修法：** `register_tool` 的默认值改为 `read_only=False`，即一个工具除非声明自己只读，否则按写操作处理。经逐个核对处理函数，25 个确实只读的工具显式标成 `read_only=True`，例如 `aieng.agent_context`、`cad.critique`、`cae.prepare_solver_run`、`cae.compare_runs` 和材料查询。核对中还发现 `aieng.refresh_semantics` 会调用 `save_project`、`opt.topology_to_sizing` 会写研究信封，它们都归为写操作。以后标错时，代价只是多串行一次，而不是悄悄写坏包。

新增测试：核心循环中的写工具不得声明 `readOnlyHint: true`；未声明的工具对同一项目的两次并发调用必须串行。后端全量测试 2249 passed、6 skipped。

**修复后复测**（同一任务，3 次，`fix` 组）：

| 组 | 成功 | 中位成本 | 中位工具调用 | `unbound_setup_faces` | 包损坏 |
|---|---|---|---|---|---|
| 基线（`main`） | 3/3 | $0.66 | 29 | 1/3 | 0/3 |
| 精简契约 | 3/3 | $0.63 | 25 | 2/3 | 1/3 |
| 精简契约 + 只读修复 | 3/3 | $0.32 | 17 | 0/3 | 0/3 |

修复后，agent 仍会在同一消息中发出两个写调用（例如 `cae.extract_solver_results` 和 `cad.edit_parameter`），但两者现在都按写操作处理，会依次拿到项目锁。成本和调用次数的下降与“不再需要从竞争中恢复”一致，但每组只有 3 次，不能当作定量结论。

## 发现 2：修改后的面重新绑定失败，修复发现 1 后未再出现

厚度修改后，`cae.generate_solver_input` 在 baseline-2、after-1、after-3 中都返回了 `unbound_setup_faces`。agent 最后都恢复了，但用的是手写 `cae_mapping.json` 或重跑 `cae.setup_static` 的方式。其中 after-1 和 after-3 同时出现了发现 1 的并行写。修复发现 1 后的 3 次复测中，这个错误一次也没有出现，与“由竞争引起”一致；但 3 次样本不足以排除其他原因，baseline-2 的失败也没有逐一追查。

## 复现

```bash
git worktree add --detach <dir> <commit>
python scripts/bench/agent_ab/run_trial.py --checkout <dir> --arm <name> --index 1 --out <runs_dir>
python scripts/bench/agent_ab/aggregate.py <runs_dir>
```

## 未验证

- 只测了 Claude Code + Sonnet 5.5。Codex、Cursor 等一次性加载全部工具 schema 的客户端没有测；98 个工具的 schema 约 36k token，本次没有改。
- 只测了一个任务（支架静力 + 修改）；其他任务（装配、拓扑优化、热分析）的契约覆盖靠静态测试，没有行为测量。
