# AIENG 战略分析报告：从安全插件到工程 Copilot 证据执行层

> **Historical — kept for context, do not build from this.** A 2026-05-19 strategy assessment.
> Where this disagrees with [`AGENTS.md`](../AGENTS.md), AGENTS.md is right.
> See [`README.md`](README.md) for what is current.

> 日期：2026-05-19
> 作者：架构顾问分析
> 面向：AIENG 核心团队
> 状态：批判性评估，非附和性意见

---

## A. 对当前战略判断的总体评价

### 核心结论

**AIENG 应该升级，但必须升级到一个非常精确的位置——不是更高，而是更宽、更深，同时边界更清晰。**

| 问题 | 结论 |
|------|------|
| 是否应该从"安全插件"升级？ | **是。** 纯安全/验证插件对用户价值上限太低，无法形成使用粘性。 |
| 应该升级到什么？ | **工程 Copilot 的证据优先执行与审查层（Evidence-First Copilot Execution & Review Layer）。** 核心能力：理解工程上下文 → 提出修改建议 → 执行前验证 → 人工审批 → 工具执行 → 结果写回证据 → 自动标记旧证据失效 → 生成审查报告。 |
| 不应该升级到什么？ | **全栈 CAD/CAE 平台。** 不应成为 mesher、不应成为 solver、不应成为 CAD kernel、不应成为 PLM。不应与 Ansys、Siemens、Dassault、Onshape、SimScale 正面竞争。 |

### 对你当前定位的批判性评估

你提出的定位：

> "AIENG 是面向 CAD/CAE Copilot 的证据优先执行与审查层。"
> "AIENG turns CAD/CAE workflows into reviewable Copilot loops."

**我的判断：这个定位方向正确，但表述过于防守。**

问题在哪里？"Evidence-First" 和 "Reviewable" 是工程安全的正确约束，但如果对外叙事只有这些，用户听到的仍然是"又一个审计工具"。你需要在对外叙事中强调闭环价值：**AIENG 让工程师用自然语言驱动设计迭代，同时自动保证每一步可审计、可复验。**

**更精确的定位表述（建议）：**

> **对内（技术边界）：** AIENG 是 `.aieng` 语义包 + 证据生命周期 + 审批执行运行时 + MCP/skill adapter 的集合。不替代 CAD/CAE 内核，但编排它们。
> 
> **对外（用户价值）：** AIENG 是开源的本地优先工程 Copilot 工作台。工程师用自然语言或半结构化指令提出设计目标，AI 提出修改建议、运行仿真、对比结果、生成报告——每一步都经过预审、审批和审计。

### 关于 Roadmap 中"Execution Boundary"的再谈判

当前 `docs/roadmap.md` 明确声明：

> ".aieng core is not intended to become a mesher, solver, optimizer, planner, or agent runtime."

**我的判断：这个边界在前 30 个 Phase 是正确的，但现在它正在从"保护性约束"变成"增长性束缚"。**

- `.aieng` **仍然不应**成为 mesher、solver、optimizer——这部分是对的。
- 但 `.aieng` **必须成为** planner 和 agent runtime 的**编排契约**。否则，证据包只是静态文档，无法驱动闭环。

**建议修正：**

> `.aieng` core 不执行几何编辑、网格生成、求解计算或优化搜索。但 `.aieng` core **描述、引用、配置、记录**执行计划（plan）、执行步骤（run）、审批决策（approval）、执行结果（evidence）和审计轨迹（audit trail）。执行本身委托给外部 CAD/CAE 工具，由 AIENG runtime 统一编排。

---

## B. 对六类外部项目/趋势的辩证分析

### 1. Human-in-the-Loop 是工程 AI 的刚需

**这个判断基本成立，但需要分层，不能一刀切。**

| 维度 | 分析 |
|------|------|
| **值得吸收的精华** | 工程的高后果性质确实要求人类在关键决策点保有否决权。完全自主 Agent 在仿真领域不被信任（SimScale 2026 报告：完全自主 Agent 采用率仅 ~10%）。"建议 → 审批 → 执行"三步模型是正确的工程伦理底线。 |
| **需要规避的糟粕** | "所有操作都需要人工审批"会杀死用户体验。如果每次 AI 建议修改一个圆角半径都要弹窗确认，工程师宁愿不用。过度审批会让 Copilot 沦为"AI 秘书"而非"AI 助手"。 |
| **对 AIENG 的具体启示** | **必须建立审批分级制度（Approval Tiering）：**<br>• **L0 自动执行：** 纯计算、只读报告、JSON diff、design target comparison——无需审批。<br>• **L1 通知后执行：** 低风险元数据操作（标记 stale evidence、写 completeness report）——执行后通知。<br>• **L2 建议确认：** 中风险参数修改（CAD 厚度调整、mesh size 修改）——UI 中展示 diff，一键确认。<br>• **L3 强制审批门：** 高风险操作（solver 执行、claim 推进、证据脚手架写入、直接 B-rep 编辑）——显式审批，需人类点击"批准"。<br><br>当前 `aieng-ui` runtime 已有 `requires_approval: bool`，但没有分级语义。建议将 `requires_approval` 扩展为 `approval_tier: "auto" | "notify" | "confirm" | "gate"`。 |
| **立即行动？** | 是，高优先级。Phase 38/39 应该引入审批分级，而不是默认所有修改都走 gate。 |

### 2. MCP 可能是工程 Agent 的标准接口

**MCP 是重要趋势，但不是产品核心，也不解决工程安全问题。**

| 维度 | 分析 |
|------|------|
| **值得吸收的精华** | MCP 作为工具暴露的标准协议，确实降低了 Agent 与 CAD/CAE 工具集成的摩擦。FreeCAD MCP、SolidWorks MCP、Onshape MCP 等生态正在形成。AIENG 已有 `aieng_freecad_mcp`，卡位正确。 |
| **需要规避的糟粕** | **MCP 本身不保证任何工程安全。** MCP 只定义"如何调用工具"，不定义"什么条件下可以调用"、"调用后如何审计"、"如何标记证据失效"。如果 AIENG 把所有差异化都押在"我们有更多 MCP adapters"上，会陷入低价值 adapter 堆量的陷阱。另外，MCP 的 tool description 是自由文本，没有 capability manifest、没有版本控制、没有 preflight contract。 |
| **对 AIENG 的具体启示** | AIENG 不应该做 10 个 MCP adapter。应该做 **2-3 个 reference adapter（FreeCAD + CalculiX/Gmsh）**，用它们证明"MCP + `.aieng` evidence + approval gate"的完整闭环模式。然后开放 adapter contract，让社区或企业按需扩展。<br><br>AIENG 在 MCP 之上的真正增值是：<br>1. **Capability Manifest：** 每个 adapter 注册时能声明自己的能力边界（L0-L5）。<br>2. **Preflight Contract：** tool 调用前自动检查输入是否满足 `.aieng` 包中的约束。<br>3. **Approval Gate：** 高风险操作必须人工确认。<br>4. **Evidence Writeback：** 执行结果自动写回 `.aieng` 包，不是散落在外部文件。<br>5. **Stale Evidence Propagation：** 上游修改后，下游证据自动标记失效。 |
| **立即行动？** | 中等优先级。继续维护 FreeCAD MCP，但**不要**在 Phase 1 新开 SolidWorks/Onshape adapter。先把 FreeCAD + CalculiX 闭环跑通。 |

### 3. CAD-GPT / Artifex 类项目的启示

**"生成可编辑逻辑而非直接生成 STEP"是正确的，但需要区分场景。**

| 维度 | 分析 |
|------|------|
| **值得吸收的精华** | CAD-GPT 的"Generate the logic of generation"是核心洞察。直接生成不可编辑几何（如纯 STEP mesh）会让工程师失去参数化控制权。Artifex 证明自然语言入口能降低门槛。 |
| **需要规避的糟粕** | OpenSCAD 的 CSG 语法对工程 CAD 来说太弱——它适合 3D 打印 hobbyist，不适合有 B-rep、特征树、约束系统的工程零件。如果 AIENG 生成 OpenSCAD 代码，工程师无法在 FreeCAD/SolidWorks 中继续参数化编辑。 |
| **对 AIENG 的具体启示** | AIENG 在 CAD 修改环节应该生成的中间产物优先级：<br><br>1. **FreeCAD Python 脚本（首选）：** 直接操作 FreeCAD 的 Part/PartDesign API，保留特征树。适合已有 FreeCAD 工作流的用户。<br>2. **CadQuery 脚本（次选，更现代）：** 纯 Python、声明式、版本控制友好。适合代码驱动设计的工程师。但 CadQuery 和 FreeCAD 的互操作性仍有摩擦。<br>3. **参数化 diff（推荐用于审查）：** 不生成完整脚本，而是生成 `"back_wall.thickness: 20mm → 10mm"` 这样的结构化 diff。工程师在 UI 中审查参数变更，点击批准后由 runtime 调用 FreeCAD API 执行。这是最可审查的形式。<br>4. **FeatureScript（Onshape 场景）：** 如果未来做 Onshape adapter，这是自然选择。<br>5. **直接 B-rep 编辑（避免）：** 丢失参数化、不可审查、不可复用。<br><br>AIENG 应该把 AI 生成的修改建议和 `.aieng` evidence 结合的方式：<br>• 修改建议必须引用 `parsed_features.json` 中的 feature ID。<br>• 建议必须声明对哪些 design targets 产生影响。<br>• 建议必须预估对 mass/stress/SF 的影响（来自 Phase 36 的 `expected_impact`）。<br>• 执行后，新的 geometry evidence 和旧的 evidence 通过 `revalidation_status` 关联。 |
| **立即行动？** | 是。Phase 38 的 closed-loop 应该优先实现"参数化 diff → 人工批准 → FreeCAD API 执行"，而不是让 AI 生成任意 Python 脚本。 |

### 4. Foam-Agent / OpenFOAMGPT / ChatCFD 类项目的启示

**可以吸收 workflow decomposition 思想，但不能照搬其黑盒自动化。**

| 维度 | 分析 |
|------|------|
| **值得吸收的精华** | 这些项目展示了 LLM 可以分解复杂 CAE 工作流：case setup → mesh check → BC mapping → solver settings → run → parse logs → diagnose → post-process → report。AIENG 的 skill layer 可以参考这种分解粒度。 |
| **需要规避的糟粕** | 1. **过度宣传 autonomy：** 很多项目暗示"AI 可以自主完成 CFD"，这在工程上不负责任。2. **环境依赖地狱：** OpenFOAM 的安装、版本、并行配置极其复杂，不适合作为开源项目的默认 solver。3. **成功标准模糊：** "solver finished" ≠ "results are correct" ≠ "design targets met" ≠ "claim can be advanced"。这些项目往往把 solver 输出直接当成 engineering validation。 |
| **对 AIENG 的具体启示** | **优先做 CalculiX + Gmsh 的结构仿真闭环，而不是 OpenFOAM。** 原因：<br>• CalculiX 轻量、开源、和 FreeCAD FEM workbench 集成成熟。<br>• Gmsh 作为 mesher 命令行友好，已有 `.aieng` mesh handoff contract。<br>• 结构静力学（linear static）比 CFD 更容易建立可重复的基准。<br>• OpenFOAM 的 turbulence、convergence、mesh quality 问题会让 AI 幻觉放大。<br><br>**必须严格区分三个概念：**<br>• **Solver success：** 求解器完成计算，没有 numerical error。<br>• **Result plausibility：** 结果在物理上合理（应力集中位置正确、数量级正确）。<br>• **Design target satisfaction：** 结果满足 `design_targets.yaml` 中的约束。<br>• **Claim advancement：** 以上全部满足 + 人工审查通过，才能推进 claim。<br><br>AIENG 当前的设计（Phase 36/37）已经体现了这种分层：`recommendation` 是假设，`verification` 是预执行启发式检查，`resimulation` 才是权威验证。这个分层应该坚持到底。 |
| **立即行动？** | 是，但选择 CalculiX 而非 OpenFOAM 作为闭环 MVP 的 solver。 |

### 5. SolidDesigner / Breptera 这类全栈平台的启示

**绝对不要追求全栈平台。这是死亡陷阱。**

| 维度 | 分析 |
|------|------|
| **值得吸收的精华** | SolidDesigner 的 AI Assistance 路线图（约束推断、特征意图检测、设计空间探索、求解器配置建议）提供了很好的功能菜单。可以从中选择 AIENG 未来 18 个月内可能实现的功能子集。 |
| **需要规避的糟粕** | 全栈 CAD/CAE 平台需要 10 年 + 数亿投入 + 大型工程团队。OpenCascade 的 B-rep 可靠性、约束求解器、大型装配体性能、drafting/GD&T、CAM 集成——每一项都是深坑。AIENG 如果进入这个战场，会在 18 个月内耗尽资源且没有任何可工作产出。 |
| **对 AIENG 的具体启示** | AIENG 应该只做三层：<br><br>1. **编排层（Orchestration Layer）：** 计划生成、步骤调度、审批管理、结果收集。`aieng-ui` runtime 正在做这个。<br>2. **证据层（Evidence Layer）：** `.aieng` 包格式、schema、验证、stale propagation、audit trail。这是 AIENG 的护城河。<br>3. **Adapter 层（Adapter Layer）：** 通过 MCP 或直接 API 调用外部 CAD/CAE 工具。薄层，不承载业务逻辑。<br><br>**不做的事：** CAD kernel、mesher、solver、optimizer、PLM、大型装配体管理、GD&T、CAM。<br><br>**差异化：** Ansys 卖 solver，Siemens 卖平台，AIENG 卖**可信的闭环编排**。企业用 AIENG 不是因为它的 solver 更快，而是因为它让 AI 驱动的设计迭代变得可审计、可批准、可复验。 |
| **立即行动？** | 否。把"全栈平台"明确写入"不做清单（Not-To-Do List）"。 |

### 6. DDC Skills / Agent Skill Library 的启示

**Skill library 是正确的组织形式，但要警惕"生态幻觉"。**

| 维度 | 分析 |
|------|------|
| **值得吸收的精华** | DDC Skills 的 221 个技能覆盖了建筑行业的完整数据流。AIENG 的 `aieng-agent-skills` 已经采用 SKILL.md 契约形式，方向正确。Skill 是分解复杂工程问题的正确粒度。 |
| **需要规避的糟粕** | "堆数量但质量低"的生态幻觉。很多项目宣称"我们有 200+ skills"，但每个 skill 都是 50 行提示词，没有输入 schema、没有失败处理、没有证据写回规则、没有测试。这种 skill 库对工程师没有价值，对 demo 有欺骗性。 |
| **对 AIENG 的具体启示** | 先定义 **5 个高价值闭环 skill**，每个都经过完整测试和基准验证：<br><br>1. **`cae-preflight`：** CAE 设置预检（缺失项、悬空引用、单位一致性、材料/载荷匹配）。<br>2. **`design-target-review`：** 读取 `design_targets.yaml` + `result_summary.json`，生成 gap analysis。<br>3. **`cad-mod-propose-verify`：** CAD 修改提议（Phase 36）+ 预执行验证（Phase 37）。<br>4. **`solver-run-orchestrate`：** mesh handoff → solver run → FRD extraction → computed_metrics refresh。当前 runtime 的 `_INTENT_MAP` 已经包含这些步骤，需要封装成 skill。<br>5. **`evidence-report-synthesize`：** 读取全包证据，生成 CAE Review Report（Phase 39 已有）。<br><br>每个 skill 的契约必须包括：<br>• 输入 schema（读取 `.aieng` 中的哪些资源）<br>• 输出 schema（生成哪些新资源）<br>• 拒绝条件（什么情况下必须失败而不是猜测）<br>• 成功标准（什么条件下可以标记完成）<br>• 证据写回规则（写回 `results/` 的哪些文件）<br>• 副作用声明（是否标记 stale、是否创建 claim proposal） |
| **立即行动？** | 是。Phase 38 的 closed-loop copilot skill 应该被重构为这 5 个 skill 的组合编排，而不是一个巨型 monolithic skill。 |

---

## C. AIENG 的最佳产品定位

### 定位选项比较

| 定位 | 描述 | 优势 | 劣势 | 适用性 |
|------|------|------|------|--------|
| **1. Engineering Copilot Evidence Layer** | 只做证据格式 + 审计 + 验证，不碰执行。 | 边界极其清晰；技术债务低；容易标准化。 | 价值感弱——用户感受不到"Copilot"，只感受到"又一种文件格式"。难以吸引个人用户。 | ❌ 太窄，正是你目前担心的问题。 |
| **2. Local CAD/CAE Copilot Workbench** | 开源、本地优先、工程师直接使用的 Web 工作台。FastAPI + React + FreeCAD + CalculiX。 | 用户价值直接；demo 震撼；社区吸引力强；数据不离开本地（企业友好）。 | 需要维护 UI/UX；需要处理 FreeCAD/CalculiX 环境配置；支持负担重。 | ✅ **主定位推荐。** |
| **3. Adapter + Skill Ecosystem for Engineering Agents** | 专注做 MCP adapter + skill library，让其他 Agent 平台（Claude Code、Cursor、AutoGen）调用。 | 杠杆效应强；如果 MCP 成为标准，AIENG 是基础设施；维护成本低（无 UI）。 | 没有直接用户；依赖其他平台的分发；容易被大平台内置功能替代。 | ⚠️ **辅助定位，不做主路线。** |
| **4. Safety-first Simulation-Driven Design Loop** | 强调 simulation-driven design 闭环：仿真结果 → 设计建议 → 修改 → 重仿真 → 对比。 | 技术叙事强；差异化明显；对标 SolidDesigner/SimScale 的 AI 方向但开源免费。 | "Simulation-driven design" 术语对非仿真工程师门槛高；容易被误解为优化器。 | ✅ **作为技术叙事子品牌，但不作为主产品名。** |

### 推荐定位

**主定位：Local CAD/CAE Copilot Workbench（本地优先工程 Copilot 工作台）**

- 对外产品名：AIENG Workbench
- 核心叙事："用自然语言迭代工程设计，每一步都可审计、可批准、可复验。"
- 用户画像：机械工程师、仿真工程师、结构设计师、工程学生。
- 关键卖点：开源、本地运行（数据不出境）、FreeCAD/CalculiX 零费用、AI 辅助但不替代判断。

**辅助定位：Engineering Agent Infrastructure（工程 Agent 基础设施）**

- 对外技术品牌：`.aieng` Format + AIENG Skills
- 核心叙事："如果你的 Agent 需要理解工程包、执行仿真、记录审计轨迹，用 AIENG 的契约和 skill。"
- 用户画像：Agent 开发者、企业内部 AI 平台团队、MCP 生态建设者。
- 关键卖点：`.aieng` 是工程领域的"结构化证据语言"；skill 是可测试、可复用的工程能力单元。

---

## D. 建议的技术架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AIENG Copilot Workbench                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  UI Layer (React SPA)                                               │   │
│  │  • Project panel, CAD viewer (Three.js), CAE lifecycle panel        │   │
│  │  • Recommendation review panel (proposal + verification verdict)    │   │
│  │  • Approval gate UX (L0-L3 tiered confirmation)                     │   │
│  │  • Chat / orchestration panel                                       │   │
│  │  • Report viewer (CAE Review Report, comparison report)             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    ↑↓                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  AI Planner / Recommendation Layer                                  │   │
│  │  • Intent parser (已有 _INTENT_MAP)                                  │   │
│  │  • Plan builder (recommend → verify → execute → resimulate → compare)│   │
│  │  • LLM-backed proposal generator (Phase 36)                         │   │
│  │  • Design target gap analyzer (Phase 35)                            │   │
│  │  **不负责：** solver 计算、mesh 生成、直接 B-rep 编辑                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    ↑↓                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Orchestration / Runtime Layer (FastAPI)                            │   │
│  │  • RunRecord, ToolCall, ToolResult, RuntimeEvent (已有)             │   │
│  │  • Approval gate engine (L0 auto / L1 notify / L2 confirm / L3 gate)│   │
│  │  • Skill registry & dispatch (skill → tool sequence mapping)        │   │
│  │  • Audit log persistence (append-only, signed)                      │   │
│  │  **不负责：** LLM 推理、CAD kernel 调用（委托 adapter）、solver 计算   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    ↑↓                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Skill Layer (aieng-agent-skills)                                   │   │
│  │  • cae-preflight                                                    │   │
│  │  • design-target-review                                             │   │
│  │  • cad-mod-propose-verify                                           │   │
│  │  • solver-run-orchestrate                                           │   │
│  │  • evidence-report-synthesize                                       │   │
│  │  每个 skill = SKILL.md + 输入 schema + 输出 schema + 测试            │   │
│  │  **不负责：** 直接执行工具调用（委托 runtime）                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    ↑↓                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Adapter Layer (MCP + Direct API)                                   │   │
│  │  • FreeCAD MCP / Direct Python API (aieng_freecad_mcp)              │   │
│  │  • CalculiX adapter (cli wrapper + input deck generator)            │   │
│  │  • Gmsh adapter (mesh handoff contract executor)                    │   │
│  │  • Future: Onshape MCP, SolidWorks MCP, Abaqus adapter...           │   │
│  │  **不负责：** 业务逻辑、审批决策、证据管理                            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    ↑↓                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Evidence / Package Layer (.aieng)                                  │   │
│  │  • Package format (ZIP + JSON/YAML resources)                       │   │
│  │  • Schema registry (design_targets, result_summary, evidence_index) │   │
│  │  • Revalidation status propagation (上游修改 → 下游证据 stale)        │   │
│  │  • Claim proposal management (只读推荐，不自动推进)                   │   │
│  │  • Audit trail (每个 RunRecord 写回 package)                        │   │
│  │  **不负责：** 执行外部工具、LLM 推理、UI 渲染                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 各层边界说明

| 层级 | 负责 | 不负责 | 理由 |
|------|------|--------|------|
| **UI Layer** | 渲染、用户交互、审批 UX、报告展示 | LLM 调用、工具执行、包修改 | 纯前端，通过 REST API 与 backend 通信 |
| **AI Planner** | 意图解析、计划生成、建议排序、gap 分析 | 直接调用 FreeCAD/CalculiX、修改文件系统 | 必须受 runtime 约束，不能绕过审批门 |
| **Runtime** | 步骤调度、审批判断、事件记录、结果收集 | LLM 推理、CAD kernel、mesh/solve | 是"交通规则"而非"车辆" |
| **Skill Layer** | 行为契约、输入输出定义、失败条件 | 工具实现、环境管理 | skill 是"菜谱"，runtime 是"厨房" |
| **Adapter Layer** | 工具调用、参数转换、stdout/stderr 捕获 | 业务逻辑、审批、证据语义 | 薄层，可替换 |
| **Evidence Layer** | 数据结构、schema、验证、stale 传播、审计 | 执行、推理、渲染 | 是"账本"，不是"工厂" |

---

## E. 最小可行闭环 MVP

### 对你提出的 MVP 的评估

你提出的流程：

> 1. 导入 CAD → 2. 生成 `.aieng` → 3. 读取 CAE result → 4. 提出减重建议 → 5. 检查 protected features → 6. 人工批准 → 7. 修改 CAD 参数 → 8. 旧 mesh/result 标记 stale → 9. 重新 mesh → 10. 重新跑 CalculiX → 11. 提取 stress/displacement/mass → 12. 和设计目标对比 → 13. 生成 review report。

**我的判断：这个 MVP 方向正确，但第 9-10 步（重新 mesh + 重新跑 solver）在 3 个月内是最大风险点。**

原因：
- Gmsh 的自动化 meshing 对任意拓扑变化不够鲁棒（特征删除后 face ID 变化、mesh size field 需要重新配置）。
- CalculiX 运行虽然轻量，但从 `.inp` 生成到 FRD 解析需要完整的 pipeline。
- 如果 mesh 失败或 solver 发散，整个闭环就断了，MVP demo 会尴尬。

**建议的 MVP 变体（降低风险但不降低价值）：**

**路径 A：Full Closed Loop（高风险高回报，3 个月紧但可行）**

前提：使用 FreeCAD FEM workbench 的已有 Gmsh/CalculiX 集成，不从头写 mesh pipeline。

1. ✅ 导入 CAD（FreeCAD converter，Phase 20）
2. ✅ 生成 `.aieng`（已有）
3. ✅ 读取已有 CAE result（已有 FRD extraction）
4. ✅ 提出减重建议（Phase 36）
5. ✅ 检查 protected features（Phase 37）
6. **新增：** UI 中展示 proposal + verification verdict，等待工程师一键批准
7. **新增：** 批准后，runtime 调用 FreeCAD API 修改参数（thickness/diameter），导出新的 STEP
8. **新增：** `.aieng` 中旧 `simulation/` 和 `results/` 标记 stale（`revalidation_status`）
9. **新增：** 调用 FreeCAD FEM + Gmsh 自动 remesh（利用 FreeCAD 内置 FEM meshing，而非独立 Gmsh CLI）
10. **新增：** 调用 CalculiX 运行（`.inp` 已由 FreeCAD FEM 生成）
11. ✅ FRD 提取（已有）
12. ✅ Design target 对比（Phase 35）
13. ✅ 生成 review report（Phase 39）

**路径 B：Staged Loop（更安全的 3 个月 MVP）**

如果路径 A 的 remesh 风险太高，可以先做：

- 步骤 1-8 同上（CAD 修改 + stale 标记）。
- 步骤 9-10 **替换为：** 生成新的 solver input deck（`.inp`），但不自动运行 solver。工程师可以选择"一键运行"或"导出到外部运行"。
- 步骤 11-13：如果工程师在外部运行了 solver，可以导入新的 FRD 并继续对比。

**我的推荐：先按路径 B 做，确保 3 个月内能 demo；然后在路径 B 的基础上，加一个"一键重跑"按钮实现路径 A。**

### 具体实现方案（路径 B + 可选路径 A）

#### 技术组件

| 组件 | 实现 | 状态 |
|------|------|------|
| CAD 修改执行器 | `aieng_freecad_mcp` standalone mode 或 `aieng-ui` backend 直接调用 FreeCAD Python API | 已有基础，需封装为 runtime tool |
| Stale 标记 | 扩展 `revalidation_status.py`，支持批量标记 `simulation/*` 和 `results/*` | 已有，需接入 runtime |
| Mesh 生成 | FreeCAD FEM workbench 的 Gmsh 集成（`femmesh_gmsh_tools`）或独立 `gmsh` CLI | 已有 mesh handoff contract，需闭环 |
| Solver 运行 | `ccx` CLI wrapper（`subprocess.run`）| 已有 runtime intent |
| FRD 提取 | `aieng` 已有的 FRD parser | 已有 |
| Design target 对比 | `aieng compare-design-targets` CLI | 已有 |
| Review report | `aieng-ui` CAE Review Report Assistant | 已有，需扩展为"闭环报告" |

#### UI 流程设计

```
[Project Panel]
  └── 导入 bracket.FCStd → 转换 → .aieng 包

[CAE Panel]
  ├── 已有结果展示（stress plot, mass, displacement）
  ├── 【新增】AI Recommendation Card
  │     ├── "back_wall thickness: 20mm → 10mm"
  │     ├── Expected impact: "-0.755 kg, predicted SF 3.98"
  │     ├── Verification verdict: ✅ PASS (Phase 37)
  │     └── [Review Details] [Approve] [Reject]
  └── 【新增】Closed-Loop Status Tracker
        ├── Step 1: Propose ✅
        ├── Step 2: Verify ✅
        ├── Step 3: Modify CAD ⏳ (waiting approval)
        ├── Step 4: Remesh ⏸️
        ├── Step 5: Re-simulate ⏸️
        ├── Step 6: Compare ⏸️
        └── Step 7: Report ⏸️

[Approval Gate Modal - L2 Confirm]
  ├── "AI proposes to modify back_wall thickness from 20mm to 10mm"
  ├── Protected features check: ✅ interface_faces unchanged
  ├── Manufacturability floor check: ✅ 10mm ≥ 1.0mm
  ├── Regression prediction: ✅ SF_after ≈ 3.98 ≥ 1.5
  ├── Impact: mass -0.755 kg
  ├── [Cancel] [Approve & Execute]
```

---

## F. Roadmap

### Phase 1：3 个月内 — 可信的闭环 Demo

**目标：** 让外部观众在 15 分钟内理解 AIENG 的价值："AI 建议 → 我批准 → AI 执行 → 我审阅新结果"。

**核心功能：**
1. **Tiered Approval Gate（分级审批）：** runtime 中 `requires_approval` 扩展为 4 级（auto/notify/confirm/gate）。
2. **CAD Modification Execution（CAD 修改执行）：** 工程师在 UI 中一键批准 proposal，backend 调用 FreeCAD API 修改参数并导出新的 STEP。
3. **Stale Evidence Propagation（证据失效传播）：** CAD 修改后，旧的 mesh、result、computed_metrics 自动标记 stale。
4. **Re-simulation Handoff（重仿真交接）：** 自动生成新的 `.inp`，支持"一键运行 CalculiX"或"导出外部运行"。
5. **Closed-Loop Report（闭环报告）：** 展示修改前后的 mass/stress/SF 对比、design target 满足度变化。

**不做什么：**
- 不做全自动 remesh（如果 FreeCAD FEM Gmsh 集成不稳定，先用手动触发）。
- 不做 SolidWorks/Onshape adapter。
- 不做拓扑优化（只改参数，不改 feature 数量）。
- 不做 OpenFOAM。

**成功指标：**
- [ ] 一个 3 分钟的屏幕录制：导入 bracket → AI 建议减重 → 批准 → 修改 CAD → 标记 stale → 生成新 inp → 运行 CalculiX → 对比结果。
- [ ] GitHub 上至少 3 个外部用户能按 quickstart 跑通同样流程。
- [ ] Phase 36 benchmark 的 `mass_reduction_recommendation` fixture 能完整跑完闭环（不仅是 recommendation）。

**主要风险：**
| 风险 | 概率 | 影响 | 应对策略 |
|------|------|------|----------|
| FreeCAD API 修改参数后拓扑不稳定（face ID 变化） | 中 | 高 | 先做 thickness 修改（拓扑不变），不做 hole removal。Phase 37b 的 geometry-kernel checks 提前到 Phase 1 做原型。 |
| Gmsh remesh 在修改后的 STEP 上失败 | 中 | 高 | 路径 B：先生成 inp，不强制自动 remesh。让用户确认 mesh 成功后再跑 solver。 |
| CalculiX 运行环境配置复杂，用户装不上 | 中 | 中 | 提供 Docker compose（FreeCAD + CalculiX + Gmsh + AIENG）；Windows 用户用 WSL2 脚本。 |
| 3 个月时间不够 | 高 | 高 | 严格按路径 B 做；"一键自动重跑"作为 Phase 1 的 stretch goal，不 blocking。 |

### Phase 2：6-9 个月 — Adapter + Skill 生态雏形

**目标：** 证明 AIENG 模式不仅适用于 FreeCAD/CalculiX，而是通用的工程 Copilot 契约。

**核心功能：**
1. **Skill Registry（技能注册表）：** 5 个核心 skill 全部可插拔、可测试、有基准。
2. **Adapter Contract v1（适配器契约）：** 发布 `docs/adapter_contract.md`，定义 capability manifest、preflight check、evidence writeback 规范。
3. **第二个 Reference Adapter：** 选择 **Onshape MCP** 或 **Abaqus CLI adapter**。Onshape 的优势是云原生、API 成熟、无本地安装；Abaqus 的优势是工业界渗透率高。
4. **Batch Loop（批量循环）：** 支持"探索 5 个设计方案"——AI 生成 5 组参数组合，逐一运行仿真，汇总对比报告。
5. **Community Skill Template（社区技能模板）：** 让第三方可以按 AIENG 契约写自己的 skill。

**不做什么：**
- 不做 SolidWorks（COM API 过于 Windows-centric，维护成本高）。
- 不做自动拓扑优化（需要 optimizer，超出编排层边界）。
- 不做云托管 solver（除非有明确商业需求）。

**成功指标：**
- [ ] 发布 `adapter_contract.md` v1.0。
- [ ] 至少 1 个外部贡献的 adapter 或 skill（哪怕是实验性的）。
- [ ] Batch loop 在 benchmark fixture 上跑出 3 个设计方案的对比报告。
- [ ] GitHub stars > 500（如果社区传播做得好）。

**主要风险：**
| 风险 | 概率 | 影响 | 应对策略 |
|------|------|------|----------|
| 外部 adapter 质量低 | 高 | 中 | adapter contract 中包含测试套件；低质量 adapter 不进入官方 registry。 |
| Onshape/Abaqus API 变更 | 中 | 中 | adapter 版本锁定 API 版本；contract 中定义版本兼容性声明。 |
| Skill 生态冷启动困难 | 高 | 中 | 先保证 5 个核心 skill 质量极高，不追求数量。 |

### Phase 3：12-18 个月 — 完整的 Copilot Loop 和社区生态

**目标：** AIENG 成为工程 Copilot 领域的开源标杆之一。

**核心功能：**
1. **Multi-Physics Expansion（多物理场扩展）：** 在结构力学闭环成熟后，增加热力学或模态分析 skill。
2. **Design Space Exploration（设计空间探索）：** 参数化 DOE + 代理模型（surrogate）+ AI 建议下一组采样点。注意：AIENG 不训练代理模型，而是调用 external Physics-ML（如 NVIDIA PhysicsNeMo）或轻量 Gaussian Process。
3. **Team Collaboration（团队协作）：** 共享 review workspace、comment on proposal、approval delegation（ senior engineer 预配置 approval policy）。
4. **Enterprise Features（企业特性）：** SSO、LDAP、audit log export（PDF/CSV）、policy as code（哪些操作可以 L0，哪些必须 L3）。
5. **Marketplace（技能市场）：** 类似 DDC Skills 的模式，但质量门槛更高。

**不做什么：**
- 不做自己的 CAD kernel。
- 不做自己的 Cloud Solver（除非商业模式明确）。
- 不做 PLM/PDM 替代。

**成功指标：**
- [ ] 至少 2 家企业用户在生产环境使用（即使是内部试点）。
- [ ] 社区贡献的 skill/adapter > 10 个。
- [ ] 一篇同行评审的论文或行业会议演讲（如 NAFEMS、AIAA、或 CAD 会议）。

**主要风险：**
| 风险 | 概率 | 影响 | 应对策略 |
|------|------|------|----------|
| 商业 CAD/CAE 厂商推出内置 Copilot，挤压空间 | 高 | 高 | 坚持"开源 + 本地优先 + 多厂商适配"的差异化。企业不会把数据送到 Ansys Cloud，但愿意用本地开源工具编排。 |
| 维护成本过高，核心团队 burnout | 高 | 高 | Phase 3 前必须建立至少 2 个核心维护者（不一定是全职）。社区治理模型（如 BDFL + 核心团队）提前建立。 |
| AI 幻觉导致工程事故，项目声誉受损 | 中 | 极高 | 永远不在营销中承诺"AI 自动验证设计安全"。所有输出都带有 honesty boundary 声明。 |

---

## G. GitHub Issue 拆解 — Phase 1

### Issue 1: P1-APPROVAL-TIERS — Implement tiered approval gate in runtime

- **Goal:** Replace binary `requires_approval` with 4-tier approval system.
- **Scope:**
  - Extend `ToolCall` dataclass with `approval_tier: Literal["auto", "notify", "confirm", "gate"]`.
  - Update `_INTENT_MAP` to assign tiers per tool.
  - Update `RunRecord` executor logic to handle each tier.
  - Update `aieng-ui` frontend to render appropriate UX per tier.
- **Non-goals:**
  - Do not change LLM/planner behavior yet.
  - Do not add policy-as-code (hardcoded tier mapping is fine for Phase 1).
- **Acceptance criteria:**
  - [ ] `aieng.validate` → auto executes, no UI modal.
  - [ ] `aieng.write_completeness_report` → auto executes, toast notification.
  - [ ] `cad.modify_parameter` → UI shows confirm bar (one-click approve).
  - [ ] `cae.run_solver` → UI shows gate modal (explicit approve + reason optional).
  - [ ] All existing tests pass.
- **Test fixtures:**
  - Mock runtime tests for each tier.
  - Cypress/Playwright UI test for confirm and gate tiers.
- **Risks:**
  - Breaking existing approval UX → mitigate with feature flag, default old behavior.

### Issue 2: P1-CAD-EXEC — Execute approved CAD modifications via FreeCAD API

- **Goal:** Close the loop from "approved proposal" to "modified CAD geometry".
- **Scope:**
  - Backend tool handler: `cad.execute_parameter_change(project_id, feature_id, parameter, new_value)`.
  - Call FreeCAD Python API (via `aieng_freecad_mcp` standalone or direct subprocess).
  - Export modified STEP.
  - Update `.aieng` package with new geometry reference.
- **Non-goals:**
  - Do not support feature removal or addition (topology change) in Phase 1.
  - Do not support roundtrip to native FCStd parametric editability (STEP export only is acceptable for MVP).
- **Acceptance criteria:**
  - [ ] Given `back_wall.thickness = 20mm → 10mm`, FreeCAD API successfully modifies and exports STEP.
  - [ ] New STEP is referenced in `.aieng` package.
  - [ ] If FreeCAD API fails, runtime emits `tool_failed` event with stderr capture.
  - [ ] Feature ID from `parsed_features.json` correctly maps to FreeCAD object.
- **Test fixtures:**
  - `examples/sample_bracket.FCStd` with known feature IDs.
  - Mock FreeCAD backend for CI (no GUI).
- **Risks:**
  - FreeCAD face/object naming unstable → use `App.ActiveDocument.getObject(label)` with fallback to ID mapping.

### Issue 3: P1-STALE-PROP — Automatic stale evidence propagation after CAD modification

- **Goal:** When CAD changes, old simulation artifacts are automatically marked stale.
- **Scope:**
  - Extend `revalidation_status.py` with `propagate_stale_on_geometry_change(package_path, changed_feature_ids)`.
  - Mark `simulation/cae_imports/*`, `simulation/runs/*`, `results/computed_metrics.json`, `results/stress_by_feature.json`, `results/result_summary.json` as stale.
  - Preserve `results/evidence_index.json` but add `stale_reason` entries.
- **Non-goals:**
  - Do not delete old evidence.
  - Do not auto-trigger remesh or re-simulation.
- **Acceptance criteria:**
  - [ ] After CAD modification, `revalidation_status.json` shows all simulation/results resources as `stale: true`.
  - [ ] `results/evidence_index.json` reflects stale states.
  - [ ] UI CAE panel shows "⚠️ Results stale — re-simulation needed" badge.
  - [ ] Old evidence files remain in ZIP for audit.
- **Test fixtures:**
  - Unit test with fixture `.aieng` package.
- **Risks:**
  - Over-marking (e.g., design_targets.yaml should NOT be stale) → whitelist non-geometry resources.

### Issue 4: P1-SOLVER-HANDOFF — Generate solver input deck and handoff to CalculiX

- **Goal:** After CAD modification, generate new `.inp` and run CalculiX (or export for external run).
- **Scope:**
  - Reuse FreeCAD FEM workbench's `.inp` export (if available in modified model).
  - OR reuse existing `cae.write_mesh_handoff` + Gmsh pipeline.
  - Runtime tool: `cae.run_solver` (already in intent map) — ensure it works with newly exported STEP/inp.
  - FRD extraction after solver completes.
- **Non-goals:**
  - Do not guarantee automatic remesh success (if Gmsh fails, show error and allow manual mesh upload).
  - Do not support nonlinear or contact analysis in Phase 1 (linear static only).
- **Acceptance criteria:**
  - [ ] Modified bracket can generate a valid CalculiX `.inp`.
  - [ ] `ccx` runs successfully on the new `.inp`.
  - [ ] FRD file is parsed and `computed_metrics.json` / `stress_by_feature.json` are updated.
  - [ ] If solver fails, runtime captures `Job-1.dat` / `Job-1.sta` for diagnosis.
- **Test fixtures:**
  - `examples/sample_bracket` with known-good solver setup.
  - Docker container with `ccx` + `gmsh` preinstalled.
- **Risks:**
  - FreeCAD FEM meshing fails on modified geometry → fallback to manual mesh upload path.

### Issue 5: P1-LOOP-REPORT — Closed-loop comparison report

- **Goal:** Generate a report comparing "before" vs "after" the closed-loop iteration.
- **Scope:**
  - Backend: `aieng.generate_loop_report(project_id, run_id_before, run_id_after)`.
  - Compare: mass, max_stress, min_safety_factor, displacement.
  - Compare: design target satisfaction (pass/fail/unknown) before vs after.
  - UI: New "Loop Report" panel or PDF export.
- **Non-goals:**
  - Do not generate PowerPoint/Word.
  - Do not include LLM-generated narrative (stick to structured data for Phase 1).
- **Acceptance criteria:**
  - [ ] Report shows table: metric | before | after | delta | target | status.
  - [ ] Report includes RunRecord IDs for audit.
  - [ ] Report is written back to `.aieng` as `results/loop_report_{timestamp}.json`.
- **Test fixtures:**
  - Unit test with mock before/after data.
- **Risks:**
  - Low risk.

### Issue 6: P1-UI-CLOSED-LOOP — Frontend UX for closed-loop tracking

- **Goal:** Engineers can visually track where they are in the loop.
- **Scope:**
  - Stepper component: Propose → Verify → Modify → Mesh → Solve → Compare → Report.
  - Each step shows status (done / current / pending / failed).
  - Current step shows actionable controls (approve button, solver run button, etc.).
- **Non-goals:**
  - Do not make it a wizard that locks user navigation.
  - Do not support batch loops (single iteration only).
- **Acceptance criteria:**
  - [ ] Stepper renders correctly on `CaePanel`.
  - [ ] Step transitions are driven by `RuntimeEvent` stream.
  - [ ] Failed steps show error detail and "Retry" button.
- **Risks:**
  - UI complexity → keep it simple, stepper can be vertical on desktop, collapsed on mobile.

---

## H. 风险与反对意见 — 站在反对者角度

### 批评 1: "这个方向太复杂了，你们做不完。"

**回应：** 如果不做闭环，AIENG 永远只是一个"工程文件验证器"，价值天花板极低。复杂性是通过**边界控制**管理的：AIENG 不做 mesher、solver、optimizer，只做编排和证据。Phase 1 的 6 个 issue 每个都可在 2-3 周内完成。如果 3 个月做不完，说明 issue 拆分不够细，而不是方向错误。

### 批评 2: "普通工程师不需要这个，直接用 ChatGPT + 手动操作就行。"

**回应：** ChatGPT 无法：1) 读取 `.aieng` 结构化证据；2) 自动标记旧证据失效；3) 保证审批和审计轨迹；4) 对比 design target 满足度。对于一次性 hobby 项目，手动操作确实够用。但对于需要**复验、审计、团队协作**的工程工作，AIENG 的闭环价值不可替代。目标用户不是"偶尔做一次仿真的学生"，而是"每周跑 10 次仿真、需要追踪设计迭代"的工程师。

### 批评 3: "大企业会自研，不需要你们的开源方案。"

**回应：** 大企业确实会自研——但它们自研的是**适配内部 CAD/CAE 工具的 adapter** 和**企业策略**。它们不会自研`.aieng` 证据格式、不会自研通用 skill contract、不会自研审批运行时。AIENG 的定位是"开源的编排与证据基础设施"，企业可以在其上构建私有适配器和策略。这与 Kubernetes 的逻辑相同：大厂自研容器编排的动机很低，因为底层基础设施开源后，差异化在上层。

### 批评 4: "普通用户门槛太高，装 FreeCAD + CalculiX + Gmsh 太麻烦。"

**回应：** 这是真实问题。Phase 1 必须提供 **Docker Compose 一键启动** 或 **WSL2 安装脚本**。长远来看，可以提供云端 sandbox（但不存储用户数据）。另外，AIENG 的 skill 和 adapter 层允许用户只用其中一部分：如果用户已经有 Abaqus 环境，他只需要 `.aieng` 包格式 + runtime + Abaqus adapter，不需要 FreeCAD。

### 批评 5: "开源项目维护成本太高，你们 burnout 后项目就死了。"

**回应：** 这也是真实风险。 mitigation：1) Phase 2 前必须建立至少 2 名核心维护者；2) 模块化架构（六层分离）让社区可以只贡献某一层；3) 不要追求 stars 数量，要追求**企业用户和贡献者**的质量；4) 如果团队资源确实有限，优先保证据层（`.aieng` format + schema）和 runtime，UI 可以社区化或简化。

### 批评 6: "AI 幻觉会导致工程风险，你们负不起这个责任。"

**回应：** AIENG 的设计哲学就是**用工程流程约束 AI 幻觉**：
- 建议阶段：proposal 是假设，不是证据。
- 验证阶段：预执行启发式检查拦截明显错误。
- 审批阶段：人类必须批准高风险操作。
- 执行阶段：工具输出是客观事实（solver converged / failed）。
- 对比阶段：design target 对比是确定性计算，没有幻觉空间。
- 报告阶段：honesty boundary 声明明确告诉用户"AI 没有验证设计安全"。

如果 AIENG 被正确使用，它**降低**了工程风险——因为它强制每一步都可审查，而传统 workflow 中工程师手动操作时更容易遗漏错误。

### 批评 7: "你们无法和商业 CAD/CAE 工具竞争。"

**回应：** AIENG **不**与商业 CAD/CAE 工具竞争。Ansys 卖 solver speed，Siemens 卖 platform integration，AIENG 卖**可信的 AI 编排**。企业不会因为 AIENG 而放弃 Ansys，但会因为 AIENG 让他们的 Ansys workflow 变得可审计、可复验。AIENG 是**增强层**，不是**替代层**。

---

## I. 最终建议

### AIENG 应该坚持什么

1. **`.aieng` 证据包和证据生命周期。** 这是技术护城河，也是差异化根基。没有证据约束的 Copilot 只是聊天机器人。
2. **Human-in-the-loop 的审批文化。** 不是"为了安全而安全"，而是让工程师**信任**AI 建议的前提。
3. **开源 + 本地优先。** 工程数据不出境是企业的硬需求，这是云厂商难以复制的优势。
4. **不做 mesher / solver / optimizer / CAD kernel。** 边界即生存。

### AIENG 应该放弃什么

1. **"纯格式标准"的幻想。** 只做 `.aieng` schema 无法吸引用户，必须提供能跑通的闭环工作台。
2. **立即做 10 个 MCP adapter 的冲动。** 先做好 FreeCAD + CalculiX 闭环，证明模式。
3. **OpenFOAM 作为 Phase 1 的 solver。** CFD 的复杂性会扼杀 MVP。
4. **追求全栈平台的诱惑。** 那是 10 年 + 数亿的事。

### AIENG 应该优先做什么（未来 3 个月）

1. **分级审批门（Issue P1-APPROVAL-TIERS）。** 用户体验的瓶颈。
2. **CAD 修改执行（Issue P1-CAD-EXEC）。** 闭环的关键一跳。
3. **证据失效传播（Issue P1-STALE-PROP）。** 证据层的核心能力展示。
4. **闭环 Demo 视频。** 没有 demo，一切战略都是空谈。

### AIENG 应该延后什么

1. SolidWorks / Onshape / Abaqus adapter（Phase 2）。
2. 云托管 / SaaS 化（Phase 3）。
3. 拓扑优化 / 生成式设计（Phase 3+）。
4. LLM fine-tuning / 专用模型（当前通用模型 + RAG 足够）。

### AIENG 应该如何对外叙事

**对工程师：**
> "AIENG 是你的开源工程 Copilot 工作台。告诉它'帮我减重 10% 同时保持安全因子'，它会建议修改、检查可行性、等你批准后执行仿真、对比结果——每一步都留下审计记录。"

**对企业技术决策者：**
> "AIENG 是本地部署的工程 Agent 编排与证据基础设施。它不替代您的 Ansys/Siemens 工具，但让您的 AI 驱动设计迭代变得可审计、可批准、可复验。"

**对开发者 / Agent 构建者：**
> "`.aieng` 是工程领域的结构化证据语言。AIENG Skills 是可测试、可复用的工程能力单元。用它们构建你的 CAD/CAE Agent，不用从零写审批门和证据管理。"

### AIENG 应该如何避免路线膨胀

1. **每季度更新"不做清单（Not-To-Do List）"**，并公开发布。
2. **每个 Phase 结束时做"边界审计"**：我们写了多少行 mesher/solver/optimizer/CAD kernel 代码？如果 >100 行，说明越界了。
3. **拒绝"看起来酷但不服务闭环"的功能。** 如果一个功能不能放进 "Propose → Verify → Approve → Execute → Compare → Report" 的链条，延后做。
4. **用 benchmark 驱动开发。** 每个新功能必须有对应的 benchmark fixture 和可测量的 correctness/efficiency 指标。没有 benchmark 的功能是虚荣功能。

---

> **最后一句话：**
> 
> AIENG 最大的危险不是做得太少，而是在"安全插件"和"全栈平台"之间摇摆不定。你们已经找到了正确的中间地带——**证据优先的工程 Copilot 编排层**。现在需要做的不是重新找方向，而是**用 3 个月时间，把一个端到端的闭环 demo 砸到桌面上**。
