# CADGenBench 2026-11 试点：5/10 部分结果与待完成设计

调研及结果整合日期：2026-10-31。用户已明确批准以 **Codex coding plan、不使用 aieng MCP** 的基线替代原样参考组，额外 API 支出仍未授权。此前的 R 必需约束已被该批准取代，原样参考没有运行。主代理随后安排五个独立工作节点尝试固定题目，本报告整合已有结果，**已尝试 5/10，另 5 题未尝试，试点尚未完成**。本次整合不重跑题目、不调用模型、不进行外部基准提交。两个独立 headless 探针仍受阻，不能计作已完成的 MCP 建模任务。

**样本在任何基线推理前冻结：种子 `202611`，10 题，5 generation + 5 editing。** generation ID 为 **108、124、127、134、147**；editing ID 为 **204、206、247、248、250**。沿用此前 Python 3.11 `Random(202611)` 的已记录抽样，后续不得因失败、模型表现或执行难度换题。样本中的失败和未运行项也必须保留。

## 1. 本地材料与许可

| 材料 | 本地位置 | 固定版本 |
|---|---|---|
| 公开代码 `huggingface/cadgenbench` | `.bench/cadgenbench` | `33304cf771fc5639144b1df9611e347251052cf8` |
| 公开输入 `HuggingAI4Engineering/cadgenbench-data` | `.bench/data` | `f76f965585817c621d6ea0d150d745adf670e66e` |
| 排行榜 Space 提交实现，仅静态取证 | 任务托管证据，不纳入项目 | `06ba2de266ba0e7886cb51abda2ba48298af80ca` |

下载前已检查代码 [Apache-2.0 许可证][code-license] 和数据卡的 [ODC-BY 声明][data-card]。数据卡将来源归于 Mecado。ODC-BY 官网许可证全文请求返回 HTTP 403，本轮未完整复核其条款，后续公开再分发前须核对署名及告知义务。代码许可不能替代数据许可。

数据匿名下载，共 **360 个文件、199,025,234 字节**，逐文件记录 SHA-256。这里只含公开输入，未访问私有真值。Git 实际使用的 `info/exclude` 已追加独立行 `/.bench/`，原字节保留。该文件位于共享 Git common directory，此排除项也影响同仓库其他工作树。

## 2. 数据集实数与输入

逐个用 `yaml.safe_load` 读取 81 份 `description.yaml`，并核查所有声明的输入文件存在：

| 任务 | 数量 | 输入模态 | 实际文件 |
|---|---:|---|---|
| generation，图纸重建 | 49 | `text+image` | 描述及工程图 PNG；46 题一张，3 题两张 |
| editing，实体编辑 | 32 | `text+step` | 描述及 `input.step`；另有编辑指令、网格侧车和四视图 |

双图题为 **127、134、145**。32 个编辑任务均有 `edit_description.txt`、`input.mesh.npz` 和 `renders/{iso,front,top,right}.png`。全量盘点只核对文件存在，后续局部 STEP 检查见第 9–10 节，未加载 NPZ。

元数据的 `description`、`input_files`、`input_type` 在全部 81 题中存在。**49 份生成任务没有 `task_type` 字段**，32 份编辑任务显式声明 `editing`。统计采用评分实现的缺省 `generation` 规则，不能直接索引该字段。任务 ID 不连续，不能从编号区间推算数量。[数据卡][data-card] 的 49/32 总数与实数一致，缺省规则见 [run_summary.py][summary-source]。

数据卡说明全部 81 题构成一个评测集，没有训练/测试拆分。私有真值包含评分所需的目标实体等材料，因此公开输入不能支持完整 CAD Score 本地复现。

## 3. 工具辅助与提交合同

基准允许任意生成系统，包括无 LLM、单模型、多模型、脚本或人工建模。[数据卡][data-card] 和 [Space 界面][space-app] 均说明其系统无关性。因此 aieng/MCP 工具辅助原则上符合格式合同，实际试点是否能成功导出合格 STEP 尚未验证。公开资料未要求必须使用参考代理。

提交是一个 ZIP，根目录放 `meta.json`，并包含**全部 81 个任务目录**。每题接受 `output.step` 或 `output.stp`。未产出结果可保留空目录，计 missing、得分为零。仅含试点子集的 ZIP 不满足当前 Space 的目录集合校验，不能直接作为完整提交。[提交文档][submission-doc]、[Space 校验实现][space-submit]

`meta.json` 的五个键均须存在：

| 字段 | 当前服务端规则 |
|---|---|
| `submitter_name` | 非空字符串 |
| `submission_name` | 非空字符串，可描述整个系统 |
| `agent_url` | 字符串或 `null`，可不提供内容，不能省略键 |
| `notes` | 字符串或 `null`；换行/制表符归为空格并去首尾空白后，最多 500 字符；界面要求纯文本 |
| `agree_to_publish` | 必须是布尔值 `true`，代表同意公开排行榜行 |

服务端还检查 ZIP 路径安全和任务目录集合。上传入口只做候选存在/大小等廉价检查，**不做 OCC 解析**。空文件记录警告，损坏或不合格几何由后续评估计零。上传校验成功不等于几何有效。提交需 Hugging Face OAuth 登录。

排行榜初始状态为 `unvalidated`，方法学证据经维护者人工复核后才可能成为 `validated`，工具使用或格式通过不会自动取得验证标记。[验证流程][validation-doc]

本轮未生成基准提交包，也未同意向排行榜发布。发布身份、名称、方法说明和公开许可仍待用户确认。

## 4. 参考代理配置与安全门槛

以下为固定代码版本的静态结果，未执行代理。[默认配置][default-config]、[LLM 客户端][llm-source]、[CLI][cli-source]

- 通过 LiteLLM 接受 `provider/model`。文档列出 OpenAI、Anthropic、Gemini，源码还支持 Hugging Face Inference Providers。密钥变量包括 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY`，HF 另支持 `CADGENBENCH_HF_INFERENCE_TOKEN`。
- 模型选择优先级为显式模型、`CADGENBENCH_MODEL`、默认 `anthropic/claude-opus-4-7`。不得因省略参数而意外调用默认模型。
- 默认后端 `build123d`，可选 CadQuery；温度 0，reasoning effort 缺省使用提供方设置，CLI 可指定 `minimal/low/medium/high`。支持程度取决于模型。
- 每次调用输出预算 **16,384 tokens**，每题累计输入加输出 **1,000,000 tokens**，最多 **1,000 轮**，题目时限 **1,800 秒**，CAD 脚本 **120 秒**，单次 LLM 超时 **300 秒**。CLI 对应 `--max-tokens-per-call`、`--max-tokens`、`--max-iter`、`--max-duration`、`--timeout`、`--llm-timeout`；默认并行度 1。
- LLM 客户端默认流式传输，瞬态错误最多尝试 10 次，使用指数退避。输入历史会重发，图像历史有裁剪，Anthropic 有缓存标记。累计预算在调用前检查、返回后累加，最后一次调用可能越过 token 阈值。重试和未返回 usage 的请求也可能带来未计入本地统计的费用。

默认 token/轮数上限不能充当美元硬预算。Windows 缺少该实现使用的 `SIGALRM/setitimer`，题目剩余时间不能用此机制打断阻塞调用。付费前需要外部截止和费用控制。[agent.py][agent-source]

还有两项已确认的运行门槛：

1. 初查的基础解释器为 **Python 3.11.5**，未发现 `cadgenbench`、`build123d`、`OCP`、`trimesh`、`litellm`。追加检查使用用户指定的 conda **aieng311，Python 3.11.15**，能找到 build123d/OCP/trimesh 等 CAD 包，但未发现 open3d。CADGenBench 自身声明 **Python ≥3.12**，不能据此声称已满足其安装条件。本轮未安装依赖，实际运行边界见第 9 节。
2. 上游 LLM 模块导入时会 `load_dotenv()`，CAD 子进程调用未显式设置 `env`、`stdin=DEVNULL`，且启动参数没有 `-I`。不能原样用于本任务的后续运行。须在授权范围内处理为仅环境变量供密钥、显式环境和空标准输入、隔离模式，并确保 CAD 子进程不继承 API 密钥。`-I` 只隔离 Python 导入环境，不能替代执行不可信生成代码所需的安全边界。

本轮自建 Git 子进程均显式传入 `env` 与 `stdin=subprocess.DEVNULL`。未执行任何下载的 Python 文件。后续如需执行，必须先检查脚本，并用 `python -I`，同时检查它继续创建的子进程。

## 5. sanity checker 的真实行为

已通读公开数据中的 [`sanity_check_submission.py`][checker]。它导入 CADGenBench 的 `analyze_step`，检查候选本身，不读取私有真值，也不评估形状是否符合题目。

| 情况 | 源码行为 |
|---|---|
| 路径不存在 | stderr 输出错误，返回 2 |
| STEP 加载或分析抛出异常 | stderr 输出失败原因，返回 1 |
| 几何有效 | 返回 0；默认打印 PASS、实体/壳/面数、体积、包围盒和网格偏差 |
| `--quiet` 且有效 | 返回 0，静默 |
| 几何无效 | 返回 1，打印至多 10 条拓扑错误及剩余条数；`--quiet` 不隐藏失败 |

脚本头部声称成功时静默，与默认实现不一致，应以实现为准。依赖导入发生在参数处理前，缺包时会先导入失败，不能依赖其“路径不存在返回 2”的分支诊断安装问题。

有效性门检查 BREP 合法性、壳封闭性，以及三角网格闭合、流形和方向一致性。固定版本另有默认 50,000,000 字节 STEP 和 1,000,000 三角形资源上限。[有效性规则][validity-doc]、[实现][validity-source]。它与评分共享门函数，但本地结果还取决于安装版本和环境。因上述环境缺口，本轮没有实际运行 checker，表中是静态行为核查结果。

## 6. API 备用路线的价格与单题费用情景

本节保留先前的 API 情景估算，供另行授权时参考。**它不用于估算 Codex 订阅内任务费用，也不代表已批准 API 运行。** 当前继续方案优先考察第 7–8 节的订阅路径。

价格于 **2026-10-31** 从实际打开的官方页面取得，单位为 **美元/百万 tokens**。OpenAI 使用标准短上下文价，Gemini 使用付费 Standard 价，不假定缓存命中、Batch/Flex 折扣或免费额度。[OpenAI 官方价格][openai-pricing]、[Gemini 官方价格][gemini-pricing]

费用公式为 `Σ(每次计费输入 tokens × 输入单价 + 每次计费输出 tokens × 输出单价) / 1,000,000`。输入需包含图像和每轮重发的历史，输出需计入可计费推理 tokens。

下面的两组用量是**规划假设，未经 CADGenBench 实测**：较少迭代每题累计输入 50k、输出 6k；较多迭代累计输入 200k、输出 24k。每组已按所有调用累计，不能再乘轮数。不同模型可能需要不同用量，这些数字不代表效果相当。

| 待选模型字符串 | 输入 / 输出单价 | 较少迭代，美元/题 | 较多迭代，美元/题 | 10 题情景费用 |
|---|---:|---:|---:|---:|
| `gemini/gemini-2.5-flash` | 0.30 / 2.50 | 0.030 | 0.120 | 0.30–1.20 |
| `openai/gpt-6.1-terra` | 1.00 / 5.00 | 0.080 | 0.320 | 0.80–3.20 |
| `openai/gpt-6.1-sol` | 2.00 / 10.00 | 0.160 | 0.640 | 1.60–6.40 |

OpenAI 的 [Terra][terra-model]、[Sol][sol-model] 页面列明图像输入和文本输出。单次输入超过 272k tokens 时，其输入及缓存价变为 2 倍、输出价为 1.5 倍，上表不适用于这种请求。区域处理等附加费也未纳入。Gemini 表中输出价含 thinking tokens。

这些是按假设用量计算的情景值，**不是置信区间或账单上限**。图像计费、上下文增长、返工、隐藏推理、失败重试、模型可用性、LiteLLM/接口兼容性均未实测。未包括本地算力、税费、汇率和可选云工具费用。本轮未查阅账号配额，也未验证这些模型在目标账号上可调用。Anthropic 官方价格页返回 HTTP 403，因此不为上游默认模型给出未经核实的报价。

如另行批准 Gemini 与 Terra 双模型比较，同一组 10 题的合计情景费用为 1.10–4.40 美元。当前额外 API 支出授权为零，不启动该备用路线。

## 7. Codex 订阅可行性与原样参考组的冲突

### 官方订阅路径与费用口径

OpenAI 官方文档区分两种 Codex 登录方式：ChatGPT 登录使用订阅权限，API key 登录按 Platform API 计费。官方客户端支持 MCP，可由已登录的 Codex 编写代码并调用本地确定性 CAD 工具，无需让 CAD 后端再调用模型。[Codex 认证][codex-auth]、[MCP 支持][codex-mcp]

这说明**官方 Codex 客户端驱动工具的订阅路线在产品能力层面可行**。本报告整合节点没有检查登录方式、凭据、套餐或余额。后续主代理提供的五个工作节点用量见第 10 节，不能据此推断账号剩余额度。官方价格页明确指出 API token 单价与订阅用量分开，不能用第 6 节的美元算式估算订阅能完成多少题。[订阅计费][codex-pricing]

后续订阅试点应记录客户端型号/版本、实际模型、reasoning/speed 设置、可取得的 usage、额度耗用、执行时间和失败原因。额度数据不可取得时记为缺失，不能记成零。已有月费不换算成“每题实测 API 成本”，也不承诺订阅剩余额度足够。额度不足即暂停，不购买额外 credits、不切换 API key、不静默更换模型。现有 credits 也不能当作已授权的额外预算。

### 未改动 LiteLLM 参考代理能否直接使用订阅

**固定参考版本没有已核实的官方授权订阅直连路径，不能承诺只改模型字符串即可满足要求。** 证据分为三层：

1. 固定代码通过 `litellm.completion` 调用提供方，默认流式读取后重组结果，没有调用官方 Codex CLI/SDK，也没有第三方 ChatGPT plan usage 所需的 OAuth 注册、授权和会话桥。依赖仅声明 `litellm`，未固定版本。因此不能仅凭该代码缺少登录函数，就推断其所有依赖版本都不支持订阅。[LLM 客户端][llm-source]、[依赖声明][baseline-deps]
2. 当前 **LiteLLM 自身文档确实提供 `chatgpt/` provider**，声称通过设备码 OAuth 使用订阅，并将 Chat Completions 桥接到 Responses。该文档说明 1.106.0 还需要 `mcp` 额外依赖，首次认证要求同步主线程环境，且会移除 `max_tokens` 等输出限制字段。固定参考的默认串行路径在主线程执行，故不能把它描述成结构上完全无法接入。不过当前解释器未发现 LiteLLM，本轮也未进行登录或兼容性测试。[LiteLLM provider 文档][litellm-chatgpt]、[参考调度][cli-source]
3. **上述 LiteLLM 文档声明的默认目标是 `https://chatgpt.com/backend-api/codex`。** 当前 OpenAI 的第三方开源/本地应用授权流程要求独立注册和用户同意，使用获准的 OAuth access token 请求公开 `https://api.openai.com/v1/responses`，并明确要求该流程不要指向 ChatGPT backend-api 端点。它还要求 `store=false`、`stream=true`，不能把已有 Codex 登录缓存当成通用 API key。[官方 plan usage 概览][plan-overview]、[官方推理要求][plan-inference]

因此，LiteLLM 文档中的技术接入声明不足以证明这条路线符合所核查的官方第三方授权流程。本轮不提取或搬运任何客户端 token，不启动设备码登录，不把未核实的 backend 路线当作获批方案。这一结论针对已核查的路径，不排除以后固定依赖版本并经独立授权、适配和验证后存在合规方案。

原样参考与“使用现有 Codex 订阅、无凭据提取、无适配修改”的组合，未得到验证。用户现已批准替换该原样路线，以上调查只用于说明为何不能报告 R 成绩。改由官方 Codex 客户端生成代码后，生成循环、提示、工具反馈和 usage 口径均可能变化，必须标记为新的 B 基线。官方还提供基于正规 plan OAuth 的 app-server 集成方法，但参考代理并未使用该方法。[官方 app-server 集成][plan-appserver]

费用控制也有实质差别：LiteLLM 文档说明其 `chatgpt/` 成本表可能记录为 `$0`，对应的美元 `max_budget` 不触发。这不表示没有消耗订阅。参考代理本身只累计响应 usage，缺失字段会归零；被 provider 移除的输出上限、缺失 usage 和重试都会削弱限额的可比性。不能把这些数字与真实 API 账单直接比较。[LiteLLM provider 文档][litellm-chatgpt]、[usage 解析][llm-source]

## 8. 公平 MCP 分组与实际改模路径

以下是**待确认设计**，不是已运行结果。产品源码核查基于本分支 `42ba28c67d7ee0f2dbe7df57c7cdb193288f16d5`，没有修改产品代码。

### 已批准的 Codex-plan 基线与后续 MCP 对照

| 组 | 生成方式与工具边界 | 当前状态 |
|---|---|---|
| R，原样上游参考 | 固定 CADGenBench/LiteLLM 循环 | 用户已批准替换，未运行，不能报告其成绩 |
| B，Codex-plan 基线 | 官方 Codex coding plan，使用本地 Python/build123d，关闭 aieng MCP | 已尝试 5/10；部分近似输出及失败见第 10 节，公平性条件未满足 |
| M，MCP 实验 | 同一官方 Codex 版本、同一实际模型和设置，增加 aieng 的确定性建模/观察工具 | 拟议，尚未连接或执行 |

当前 B 基线及以后另获批准的 B/M 比较均使用**已冻结的同一组 10 个任务，5 生成 + 5 编辑**。抽样在已核查的排序 ID 集合上使用 Python 3.11 `Random(202611)`，先从 127/134/145 选一个双图题，再抽其余 4 个生成和 5 个编辑任务。ID 已在开篇冻结，不再作为候选。该样本用于覆盖输入路径，不外推为全部 81 题的平均水平。

比较组使用相同的公开任务输入，保存输入哈希，并记录实际送入各自循环的文件。B/M 应固定单一实际 Codex 模型、共同任务提示、AGENTS/技能版本和客户端默认工具权限，M 新增说明另行留档。每个组/题使用独立会话和工作目录，不共享另一组的代码、记忆或结果，交替安排配对顺序。保持相同硬件和 CAD 依赖，待并行求解不再造成资源竞争后才计时，不把本轮静态设计当作性能测量。

后续比较应统一时限、审批等待计时和停止规则，不能把先前建议的每题 1,800 秒当作本轮已落实的限制。现有 127 工作记录提及 900 秒，但尚未核验所有题目使用相同时间设置。用户要求相同工具限制，本轮指定 **每题最多 20 次工具调用**，提示没有硬执行该上限，108 已超限。因此当前结果不满足公平比较条件。后续须在新运行前落实共同的硬限制，固定模型推理强度、speed、重试和 CAD 单次超时，同时记录模型 usage、CAD 执行次数/时长和失败重试。工具调用数相等也不代表计算量相等。

M 组应使用任务专用工具白名单。候选包括必要的项目/包准备、`aieng.agent_readme`、`aieng.guide`、`aieng.agent_context`、`cad.confirm_modeling_plan`，以及 `cad.get_source`、`cad.execute_build123d`、`cad.list_editable_parameters`、`cad.propose_edit_parameter`、`cad.edit_parameter`、`cad.critique`、`cad.diagnose`。核心构建和参数修改路径已核查，其余候选的完整依赖与现场可用性仍须在固化白名单前复核。实际 wire 名由服务端将点替换为下划线，客户端前缀也需现场核对。**排除 `cad.refine`**，其实现明确要求 `ANTHROPIC_API_KEY` 并再调用 Claude；同时排除无关 CAE、网络搜索和私有真值路径。[工具注册][cad-tools]、[MCP 调度][mcp-server-source]

M 组可以包含建模辅助、结构化反馈和预览的系统收益，但不能将其全部归因为 MCP 协议本身。B 的提示、执行循环和检查器版本需留档，不能称为上游原样参考。比较组都不接受人工补画、参数答案或额外几何提示。审批只决定是否允许所述操作，拒绝和范围变化照实记录。

输出统一为每题 `output.step`，M 组从实际生成的包中提取 STEP，记录产物哈希，不能只比较 UI 缩略图。两组使用同一固定版本 sanity checker。报告按生成/编辑分别给出有效、无效、缺失及用量结果，保留所有 10 题，不能删除失败项。没有私有真值时只报告本地有效性和预先确定的观察，完整 CAD Score 继续待办。

### 编辑原始 STEP 与审批的真实边界

- **生成路径**：`cad.execute_build123d` 接收调用者编写的代码，要求结果变量为 `result`，由后端写 `geometry/source.py`、`generated.step` 等产物，本身无需模型 API key。它支持 replace/append，但 append 必须已有 `geometry/source.py`，否则返回 `append_without_base`。[建模实现][cad-generation-source]
- **参数快改路径**：`cad.edit_parameter` 按特征/参数合同替换源代码常量，再重建和记录快照，并将下游 CAE 证据标记过期。它依赖参数图、源代码和可定位常量。`proposal_id` 在当前入口可选，提供时检查目标和值是否匹配及是否过期；共享/未定域参数另需 `confirmScopeRisk`。这不等同于任意 STEP 面编辑。[参数合同][edit-contract-source]、[建模实现][cad-generation-source]
- **数据集编辑任务**：几何起点是原始 STEP，未提供上述参数化源代码。基础 STEP importer 仅复制 `source.step/normalized.step`，不恢复参数化源程序。候选方案是让同一模型从不可变输入 STEP 导入实体并编写明确几何操作，先以 replace 建立真实可执行源码，再在条件满足时使用 append/参数工具。文件暂存、导入、面选择及 STEP 输出的完整路径尚未运行验证，不能用事先手工重建的参数模型替代原题。[STEP importer][step-import-source]
- **建模计划审批**：`cad.confirm_modeling_plan` 的 schema 要求 `project_id/summary/steps`，可带 assumptions/scope，注册为 `requires_approval=True`。其 handler 只返回确认回执，不执行 CAD，也没有写入绑定后续代码的授权令牌。`cad.execute_build123d` 和 `cad.edit_parameter` 则为普通写工具，未注册逐次 approval。[工具注册][cad-tools]、[审批 schema][plan-schema-source]
- **实际审批责任**：client 模式依赖客户端权限界面；managed 模式通过后端审批 broker；elicit 模式要求客户端声明支持，否则拒绝。block 模式会阻止所有写工具。已检查的 MCP 调度和 `runtime.invoke_tool` 路径没有以持久化计划令牌验证后续构建/编辑范围。因此不能声称服务端已自动强制每次改模都落在已批准计划内。试点必须保留明确计划、真实批准及调用审计，范围变化重新审批，不能全部自动批准后再把确认回执当授权证据。[MCP 调度][mcp-server-source]、[runtime 入口][runtime-source]、[审批机制][mcp-approval-source]

仓库兼容性文档将 Codex 行标为“documented, not verified”。本轮没有验证目标 Codex 的 MCP 连接或审批弹窗，也没有绕过审批。[兼容性记录][mcp-compat-source]

还需区分现有产品与上游参考的子进程状况：产品的两个核心 build123d 执行器已显式传入 `env` 和 `DEVNULL`，但启动参数没有 `-I`；其环境函数复制整个父环境，不能视为密钥过滤。产品文档也明确说明这不是抵御恶意代码的完整沙箱。后续安全执行方式仍须在不改产品代码的约束下另行解决，不能把本轮设计当作已验证可运行。[建模实现][cad-generation-source]、[执行边界][cad-boundary-source]

## 9. 本地 headless 探针：实际结果与阻塞

新增 [`headless_probe.py`][local-probe]。使用 conda aieng311 的解释器，以 `-I -B` 运行，工作目录、HOME、临时文件、SQLite、日志和相对缓存均在任务 scratch。父启动调用显式设置不含 API 密钥的环境和 `stdin=subprocess.DEVNULL`，不连接正在运行的后端或求解器。安全钩子禁止网络、常见凭据文件读取、无显式 env 的子进程，并对 Python 子进程要求 `-I`。

脚本使用实际 `create_app(settings)` 和 `runtime.invoke_tool`。每个阶段先调用并保存 `aieng.agent_readme`、`aieng.guide(topic="cad")`；已完整读取其返回内容。保留 `AIENG_MCP_REQUIRE_GUIDES=1`，驱动脚本还要求完成这两次读取后才允许 CAD 工具。这是 **in-process 顺序检查**，没有启动 MCP transport，不能当作真实 MCP guide guard 的端到端测试。

实际调用的关键命令如下，完整 argv、工作目录、环境变量名称及 stdout/stderr 保存在本次托管证据中。`--authorized-local-probes` 只限定本地调查范围，不代表客户端计划批准：

```text
<aieng311-python> -I -B <repo>/scripts/bench/cadgenbench/headless_probe.py
  --workspace <repo> --run-root <scratch>/headless-r4b
  --phase probes --authorized-local-probes
```

父启动形式为 `subprocess.run(argv, cwd=run_root, env=sanitized_env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=120)`。底层输入检查将 `--phase` 改为 `input-check`，其余隔离设置相同。

| 探针 | 已实际完成 | 尚未执行及原因 |
|---|---|---|
| generation 108 | 读取公开图纸，临时创建项目，调用 `aieng.agent_context` 和 `cad.get_source`；返回 `status=ok, source=null, has_base=false` | 未构建图纸实体、未产生候选 STEP；真实建模计划批准未取得 |
| editing 204 | 读取修改指令，临时创建项目并取得相同的空源状态；另做输入检查 | 未移动孔、未生成编辑结果；审批与 build123d 导入路径受阻 |

**审批阻塞是实际 guide 合同，不是模型调用失败。** 返回的 CAD guide 明确写明建模请求本身不等于计划批准。`cad.confirm_modeling_plan` 的元数据确认 `requires_approval=true`，但直接 `invoke_tool` 不提供客户端批准界面。本轮没有调用确认 handler 制造“已批准”回执，也没有调用 `cad.execute_build123d` 或 `cad.edit_parameter`。两个探针均记录 `blocked_before_geometry_mutation`，输出包列表为空。

**`import_step` 路径的运行阻塞更早发生在依赖导入。** `from build123d import import_step` 进入 `IPython → platform.system() → platform.win32_ver() → _syscmd_ver()`，尝试 `cmd /c ver`。aieng311 的 `Lib/platform.py:284–290` 已设置 `stdin=DEVNULL`，但 `subprocess.check_output` 没有显式 env。审计事件显示 `explicit_env=false`，在创建子进程前以 `Child launch blocked: missing explicit env` 拒绝。因此没有执行到 `import_step(input.step)`，也没有完成 204 所要求的两孔中心距 20→30 mm 修改。未通过修改产品/标准库或放宽钩子绕过该限制。

为将“输入实体合法性”和“build123d 路径可运行”分开，另用底层 `OCP.STEPControl_Reader` 与 `BRepCheck_Analyzer` 检查 **204 原始输入**，没有更改形状。实际返回：

```json
{"read_ok": true, "roots_transferred": 1, "brep_valid": true,
 "solid_count": 1, "candidate_checked": false, "mesh_gate_checked": false}
```

输入 SHA-256 为 `fd0538f5035022fb749438f2dd121c2632c046ac05f3d48c224750e79b5ede3e`。这只证明该输入能读取且通过 BREP analyzer，**不是候选输出通过 CADGenBench 完整门**。没有检查候选水密/闭合流形网格，未运行下载的 sanity checker，完整 CAD Score 更未测量。

首次 discovery 打印时因 `-I` 忽略 `PYTHONIOENCODING` 而发生 UnicodeEncodeError；已在自有驱动中显式设定 UTF-8，并读取此前已写出的完整 guide。随后 probes 与 input-check 阶段均退出 0，含义是阻塞评估及输入检查已落盘，不表示两个任务已求解。

## 10. 获准 B 基线：五题已有结果

**仅完成部分尝试：5/10 题已尝试，5 题未尝试。** 主代理安排的五个独立 Codex 工作节点尝试 108、124、127、204、206。本次整合只读取本工作树 `.bench/pilot/<id>` 的已有证据，以及主代理转交的 PIRA Team manifest 数值，不访问兄弟运行、不启动推理、不重新执行生成代码或几何检查。先前 headless 探针与 204 输入检查不计作 B 候选结果。

### 模型、来源与计量边界

五题均记录请求模型 **`gpt-6.1-sol`**。工作节点没有暴露已解析的服务端模型 ID，因此保留“请求模型”口径，不声称独立核实了实际模型版本。本机先前只读检查得到 Codex CLI **`0.164.0-alpha.11.1`**，该检查不能证明五个工作节点均使用这个 CLI 版本。官方文档中的模型及认证配置仍只作为能力说明。[Codex 模型][codex-models-current]、[配置参考][codex-config-current]

用量来源为主代理检索的五份实际 PIRA Team manifest，由主代理在本次任务消息中转交。五份均标记 **usage completeness=true**。本节点没有直接读取原始 manifest；本地工作记录中较早的“tokens unavailable”及中途计时由该完整工作节点统计补足。**input tokens 已包含 cached input，不能再把缓存列加到输入列。** 时间是各工作节点的 elapsed time，包含调查、编排、建模、检查和报告，不是纯 CAD 耗时或计费时长。此处 usage 完整不代表工具调用记录或几何检查完整。

[`prepare_baseline.py`][prepare-baseline] 是推理前的冻结准备器，只哈希输入并独占创建初始未运行清单，不会汇总后续结果。先前实际检查了 52 个输入文件的哈希/大小，重复写入被拒绝且原清单不变。历史初始清单保留，不用它覆盖本节的后续部分结果。

### 逐题完成、有效性与迭代

| ID | 类型 | 工作节点状态 | 已有输出与局部检查 | 候选建模迭代 |
|---|---|---|---|---:|
| 108 | generation | `incomplete` | 输出 STEP 重导入 BREP 有效、1 solid；超出 20 次工具上限，不能纳入等限额比较；部分耳部、压筋等未重建 | 1 |
| 124 | generation | `completed` | 输出重导入 BREP 有效、1 solid；图纸近似，轮廓未精确约束，外缘 R0.5 倒圆失败 | 1 |
| 127 | generation | `completed` | 输出重导入 BREP 有效、1 solid；铸造过渡、部分位置近似，小筋及多数倒圆省略 | 1 |
| 134 | generation | 未尝试 | 无输出，未检查 | 0 |
| 147 | generation | 未尝试 | 无输出，未检查 | 0 |
| 204 | editing | `needs_decision` | 无候选输出；输入与指令目标疑似不匹配，只有输入检查 | 0 |
| 206 | editing | `incomplete` | STEP 已写出，重导入 BREP 有效、1 solid；随后请求几何检查断言失败，不能计为编辑成功 | 1 |
| 247 | editing | 未尝试 | 无输出，未检查 | 0 |
| 248 | editing | 未尝试 | 无输出，未检查 | 0 |
| 250 | editing | 未尝试 | 无输出，未检查 | 0 |

“候选建模迭代”指已有记录中的候选版本数，不等于工具调用数或 LLM 回合数。除 108 已知超限外，本节点没有完整逐题工具调用计数，不能推断其他题目均满足上限。`completed` 是工作节点交付状态，不代表精确复原或通过完整基准。四份输出的局部 BREP 检查不能替代第 5 节的网格门，更没有得到私有真值 CAD Score。

### 实际 manifest 用量

| ID | 请求模型 | input，含缓存 | cached input，输入的子集 | output | 工作节点秒数 | usage completeness |
|---|---|---:|---:|---:|---:|---|
| 108 | `gpt-6.1-sol` | 423604 | 395904 | 20762 | 547.825 | true |
| 124 | `gpt-6.1-sol` | 402759 | 325248 | 11466 | 340.711 | true |
| 127 | `gpt-6.1-sol` | 260274 | 230528 | 10817 | 319.832 | true |
| 204 | `gpt-6.1-sol` | 275868 | 229376 | 7859 | 222.171 | true |
| 206 | `gpt-6.1-sol` | 255985 | 224768 | 10263 | 288.871 | true |
| 已尝试五题合计 | 同一请求模型 | 1618490 | 1405824 | 61167 | 1719.410 | 五份均完整 |

134、147、247、248、250 没有尝试记录，tokens 和耗时均记为不可得，不填零。上表秒数合计是工作节点时间之和，不能当作并行运行的总墙钟时间。**实际订阅成本、额度耗用及完整试点 p90/预算预测均不可得。** 不能把五题用量按第 6 节 API 单价换算为已支出美元，也不能将近似输出、失败或未尝试项删除后推算十题成功成本。[非交互事件与 usage][codex-noninteractive-current]

### 本地命令证据及未通过检查

以下为已有工作记录的命令和输出摘录，本次未重新执行。`<aieng311-python>` 指用户目录下的 `anaconda3/envs/aieng311/python.exe`，工作目录为本仓库。原始文件均保留在忽略的 `.bench/pilot` 中，不随 PR 发布。

| ID | 已有命令/检查证据 | 观察结果 |
|---|---|---|
| 108 | `model.py`、`iteration-1.log`、`validation.json` | JSON 为 candidate 1、roundtrip_valid=true、brepcheck_valid=true、solid_count=1；本地没有独立命令清单可确认完整启动 argv。20 次工具上限违规来自主代理转交的工作结果 |
| 124 | `pira_ctx exact --intent 'Build case 124 candidate 1 and validate exported STEP BREP and connected solid' -- <aieng311-python> -I .bench/pilot/124/model.py`；`evidence.md`、`command_history.txt` | 记录 exit 0；validation.json 重导入有效、1 solid；198.8 × 74.4 × 40 mm 包围盒检查通过。R0.5 失败保留在日志中 |
| 127 | 核心调用 `<aieng311-python> -I .bench/pilot/127/model.py`；`evidence.md` 保留 `pira_ctx`、PowerShell 与 `Tee-Object` 外层命令，stdout 见 `iteration1.log` | 记录 exit 0；validation.json 为 step_brep_valid=true、step_solids=1；高度 305 mm 的 0.001 mm 容差断言通过 |
| 204 | `<aieng311-python> -I .bench/pilot/204/probe.py` 及 `-I .bench/pilot/204/inspect_holes.py`，均经 `pira_ctx exact`；`evidence.md`、`probe.json`、`holes.json` | 两次记录 exit 0，仅检查输入；未输出候选 |
| 206 | `candidate_1.py` 中记录命令 `<aieng311-python> -I -B .bench/pilot/206/candidate_1.py`；实际 `iteration-1.log` 的命令前缀与 traceback 相符，完整外层 argv 未保留于此日志 | 程序先导出、重导入并断言 BREP 有效及 1 solid，随后 line 40 的体积增量检查失败；没有生成 validation.json |

204 的指令要求 Z 轴孔沿 X 间距 20→30 mm。工作节点检查到两处小孔壁是 BSpline 面，其包围盒沿 X 相差 **30 mm**，未解析证明其轴向。另一组圆孔的轴沿 Y，沿 Z 间距 **24 mm**。这些观察尚不能识别符合原指令的起始孔对，因此保留 `needs_decision`，不改坐标约定、不换输入、不擅自指定新目标。

206 填孔后的实际体积增加 **1710.4250326972688 mm³**，程序按三个理想圆柱计算期望 **1696.4600329384882 mm³**，差约 **13.965 mm³**，超过 0.01 mm³ 容差。断言在 STEP 写出后失败。后续原材料保留、填孔位置与残余孔面等检查没有执行，不能宣称通过，也不能只因文件存在或 BREP 有效就记为成功。局部证据尚未解释差异根因。

本次只读取文件并复算哈希，四份输出如下。哈希用于定位已有产物，不代表新一轮几何验证。

| ID | STEP 字节数 | SHA-256 |
|---|---:|---|
| 108 | 405328 | `4488dbf15d836a43929b88a9f06eb018097c813219bdd5e606d2a9f1c73fe609` |
| 124 | 583688 | `ecd2d499cd190db8831be920133fae4ab1dc72da4df958623a74af7a52a3a3b0` |
| 127 | 1267773 | `421c9853e48eab3b8bb1f446836aa00bcd4f276e9e6dedd76d3ac72e8a2137f4` |
| 206 | 5364014 | `32565b8476d215d32814a1dbabe021d78b93080a5f1c3e6f1de13b26b74150de` |

### 公平性与安全限制

现有提示未硬执行用户要求的相同工具上限，108 已越过 20 次。**本轮不能作为等工具预算的公平 B/M 比较**，M 组也没有运行。不能事后删去超限调用、把工具调用混称候选迭代，或以近似结果补齐未运行题目。各题耗时和提示设置尚未形成完整统一审计，五题结果只作部分运行记录。

第 9 节两个 headless 探针仍是 `blocked_before_geometry_mutation`：直接 `invoke_tool` 缺少真实计划批准界面，build123d 依赖路径还尝试无显式 env 的 `cmd /c ver`。另一个不导入 aieng 的独立预检也观察到相同 env 阻塞。后续独立工作节点成功导入 build123d 不等于这些受保护探针已完成，也不能证明执行环境相同。现有命令摘要不足以核验每次父进程和依赖子进程的显式 env/DEVNULL，故不补称全部历史执行符合这一安全要求。不能修改产品、标准库、放宽审计钩子或伪造计划批准来消除阻塞。

## 11. 继续工作与草稿范围

1. 保留冻结种子与全部 10 个 ID。五道未尝试题为 **134、147、247、248、250**，本次不启动它们，也不重跑已尝试题。恢复时由主执行通道落实同一请求模型、正常 coding-plan 授权和硬工具限额，不能静默转 API、加购额度或变更题目。
2. 204 需要主代理确认：能否从原始固定输入识别满足描述的孔对？若需更正输入或修改目标，须另获明确批准并标记对原基准语义的影响。建议先核对固定输入与描述，不自行选择相近孔对。206 的体积增量断言仍未通过，不能改弱容差来补成成功。
3. 原样 R 未运行且已获准替换。M 组的建模计划批准通道和 env/DEVNULL/`-I` 执行边界仍待解决，guide 要求保持有效，不以直接确认 handler 替代真实批准。不得将 B 部分结果称为已完成 MCP 探针。
4. 完整 checker、私有真值评分、统一工具计数、完整十题及 p90 预测均未完成。订阅美元成本和额度继续标记不可得，保留已有失败与近似说明。
5. 按用户更新后的要求提交**英文 draft PR**，只包含本中文报告和 `scripts/bench/cadgenbench` 脚本，明确 5/10 部分尝试及全部阻塞。产品/runtime、下载源码与公开输入保持不变；数据集、生成 STEP 和本地原始日志均不入提交。未向外部基准上传结果。

[code-license]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/LICENSE
[data-card]: https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data/blob/f76f965585817c621d6ea0d150d745adf670e66e/README.md
[summary-source]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/eval/run_summary.py#L195-L207
[submission-doc]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/docs/benchmark/submission.md
[space-app]: https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench/blob/06ba2de266ba0e7886cb51abda2ba48298af80ca/app.py#L1151-L1198
[space-submit]: https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench/blob/06ba2de266ba0e7886cb51abda2ba48298af80ca/submit.py#L614-L736
[validation-doc]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/docs/benchmark/validation.md
[default-config]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/baseline/default_config.yaml
[llm-source]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/baseline/llm.py
[cli-source]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/baseline/_cli.py
[agent-source]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/baseline/agent.py
[checker]: https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data/blob/f76f965585817c621d6ea0d150d745adf670e66e/sanity_check_submission.py
[validity-doc]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/docs/metrics/cad_validity.md
[validity-source]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/common/validity.py
[openai-pricing]: https://developers.openai.com/api/docs/pricing
[gemini-pricing]: https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash
[terra-model]: https://developers.openai.com/api/docs/models/gpt-6.1-terra
[sol-model]: https://developers.openai.com/api/docs/models/gpt-6.1-sol
[paths-source]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/common/paths.py
[baseline-deps]: https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/pyproject.toml#L40-L47
[codex-auth]: https://learn.chatgpt.com/docs/auth
[codex-pricing]: https://learn.chatgpt.com/docs/pricing
[codex-mcp]: https://learn.chatgpt.com/docs/extend/mcp?surface=cli
[litellm-chatgpt]: https://docs.litellm.ai/docs/providers/chatgpt
[plan-overview]: https://developers.openai.com/siwc/token-sharing-open-source
[plan-inference]: https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
[plan-appserver]: https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server
[cad-tools]: ../../aieng-ui/backend/app/runtime_registry/cad.py
[cad-generation-source]: ../../aieng-ui/backend/app/cad_generation.py
[edit-contract-source]: ../../aieng-ui/backend/app/project_io.py
[step-import-source]: ../../aieng/src/aieng/geometry/step_importer.py
[plan-schema-source]: ../../aieng-ui/backend/app/runtime_tool_schemas.py
[mcp-server-source]: ../../aieng-ui/backend/app/mcp_server.py
[runtime-source]: ../../aieng-ui/backend/app/runtime.py
[mcp-approval-source]: ../../aieng-ui/backend/app/mcp_approval.py
[mcp-compat-source]: ../../aieng-ui/backend/docs/mcp_client_compatibility.md
[cad-boundary-source]: ../../aieng-ui/backend/docs/cad_execution_boundary.md
[local-probe]: ../../scripts/bench/cadgenbench/headless_probe.py
[prepare-baseline]: ../../scripts/bench/cadgenbench/prepare_baseline.py
[codex-models-current]: https://learn.chatgpt.com/docs/models
[codex-config-current]: https://learn.chatgpt.com/docs/config-file/config-reference
[codex-noninteractive-current]: https://learn.chatgpt.com/docs/non-interactive-mode
