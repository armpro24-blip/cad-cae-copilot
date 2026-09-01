<!-- SEO 关键词: AI CAD, AI CAE, AI CAX, Text-to-CAD, Text-to-CAE, Text-to-CAX, 生成式CAD, AI工程工作台, MCP CAD, build123d, OpenCASCADE, CalculiX -->

<div align="center">

# CAD/CAE Copilot

### 大多数 AI CAD 工具在出图那一刻就结束了。CAD/CAE Copilot 把工程规格变成真实、可编辑、可验证的 CAD —— 并让每个产物都可复现。

一个 AI 原生 CAD/CAE/CAX 工作台。支持 MCP 的 Agent 编写真实的
build123d / OpenCASCADE 几何体,导出 STEP/STL/GLB,命名零件,
暴露稳定拓扑指针,运行确定性评审,并可继续进入 CAE —— 全部保存在
一个可复现的 `.aieng` 包中。

**你需要自带 MCP 客户端**(如 Claude Code、Codex、Copilot、Cursor),
它自带模型访问权限。aieng 后端本身无需 API 密钥。

<a href="docs/assets/images/hero.webp">
  <img src="docs/assets/images/hero.webp" width="100%" alt="使用 aieng 建模并检验的工业电机安装夹具全规格模型">
</a>

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/armpro24-blip/cad-cae-copilot)
![CAD](https://img.shields.io/badge/CAD-build123d%20%2F%20OpenCASCADE-1f6feb)
![FEA](https://img.shields.io/badge/FEA-CalculiX-e36209)
![Agent](https://img.shields.io/badge/agent-MCP%20server-8957e5)
![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)

[这是什么?](#这是什么--架构与信息流) ·
[快速开始](#快速开始) ·
[CAD 示例](#工业-cad-示例) ·
[基准测试](#定量基准测试) ·
[为什么选 aieng](#为什么选-aieng--超越-text-to-cad) ·
[一条提示词安装](docs/one-prompt-agent-setup.md) ·
[MCP 设置](aieng-ui/backend/MCP_SETUP.md) ·
[Agent 指南](AGENTS.md)

<sub><a href="README.md">English</a> | 中文</sub>

<sub>真实 STEP/STL/GLB · 可编辑参数 · 命名零件 · 稳定拓扑指针 · 确定性评审 · CAD → CAE 产物 · 审批控制操作</sub>

</div>

## 一眼看懂

CAD/CAE Copilot 让支持 MCP 的 Agent 把工程规格转成可复现的 CAD/CAE 包。

- **输入:** 明确的机械规格、尺寸、约束和后续修改。
- **CAD 内核:** 真实 build123d / OpenCASCADE 几何体,不是假渲染器。
- **输出:** STEP、STL、GLB、生成源码、缩略图、拓扑映射和 `.aieng` 包。
- **可编辑性:** 命名零件、可编辑参数,以及拓扑映射成功时稳定的 `@face:*`
  指针。
- **验证:** 确定性 critique、几何检查、设计规则断言和 diff-aware edits。
- **CAE 路径:** 材料、载荷、约束、网格、求解器 artifacts 和证据与 CAD 模型
  放在一起。
- **Agent 模型:** 你自带 MCP 客户端和模型访问权限;后端本身不需要 API key。

```text
engineering spec
  -> MCP agent
  -> build123d / OpenCASCADE CAD
  -> named parts + topology pointers
  -> critique / CAE setup / solver evidence
  -> reproducible .aieng package
```

## 这是什么? —— 架构与信息流

一张图说清:一份自然语言工程规格,经由**你自己的 MCP Agent** 端到端驱动,变成
经过优化、验证的 CAD/CAE,并完整保存在一个可复现的 `.aieng` 包中。

<a href="docs/cad_cae_copilot_architecture_information_flow.png">
  <img src="docs/cad_cae_copilot_architecture_information_flow.png" width="100%" alt="CAD/CAE Copilot 系统架构与信息流:用户需求 → Agent(规划/推理)→ MCP 工具层 → CAD 建模 → CAE 设置与仿真 → 结果评估 → 闭环优化,带人类在环的信任/审批层与 .aieng 工程包"/>
</a>

**用户需求 → Agent(规划与推理)→ MCP 工具层 → CAD 建模与编辑 → CAE 设置与仿真
→ 结果评估 → 闭环优化。** 人类在环的**信任与审批**层把关每一次变更,每个结果都标注
一个**可信度等级**,所有产物都落入自描述的 `.aieng` 包 —— 让任何支持 MCP 的 Agent
都能设计、分析、优化并交付经过验证、可复现的 CAD/CAE 方案。

## 定量基准测试

项目在 [`aieng/benchmarks/datasets/analytical_fea`](aieng/benchmarks/datasets/analytical_fea)
中包含机器可读的解析 FEA 基准语料。它会构建可运行的 `.aieng` 案例,
将求解器指标与有文档说明的闭式参考解进行比较,并输出
`aieng.benchmark.analytical_fea.scorecard` JSON 产物。该测试报告的是
"在容差内与参考一致";它不是认证,也不是生产安全声明。

```bash
cd aieng
python -m aieng.benchmarks.analytical_fea --out analytical_fea_scorecard.json
```

CI 会运行解析基准语料测试,以便语料/参考漂移和 scorecard 回归能尽早失败。

## 快速开始

三种入口 —— 任选其一,几分钟即可开始建模。

> **开始前:** 你需要自带 MCP 客户端(Claude Code、OpenAI Codex、GitHub
> Copilot、Cursor……),它自带模型访问权限。aieng 后端本身无需 API 密钥 ——
> 你的 Agent 通过 MCP 连接它,并用自己的工具驱动工作台。

第一次尝试,**Docker(选项 2)最稳妥** —— 它固定了 build123d / OpenCASCADE /
CalculiX 整套依赖,你的机器上无需编译任何东西。本地开发安装更适合你打算
改代码时使用。

### 选项 1:GitHub Codespaces(最快,零安装)

点击上方 **"Open in GitHub Codespaces"**。环境会自动配置;加载完成后运行
`make dev`(若 `make` 不可用,改运行 `python3 scripts/dev.py`)。然后连接一个
Agent,粘贴[电机安装夹具提示词](#从规格到经过验证的-cad),或更短的支架提示词:

```text
Create a 120 × 80 × 12 mm machined bearing support bracket with a centered
Ø42 mm horizontal bearing bore, four Ø10 mm base mounting holes, and two
mirrored gussets. Preserve the exact dimensions, expose editable parameters,
verify the final geometry, and run the deterministic engineering critique.
```

在工作台中检查生成的模型、命名零件、验证结果和稳定的 `@face:*` 引用。

### 选项 2:Docker 一体化(推荐的本地方案)

将后端、构建好的查看器、MCP HTTP 服务器、build123d / OpenCASCADE 依赖
和 CalculiX 打包到一个容器中。

**快速开始 —— 拉取已发布镜像(无需本地构建):**

```bash
docker pull ghcr.io/armpro24-blip/aieng-workbench:latest
docker run --rm -it -p 8000:8000 -p 8765:8765 -v aieng-data:/data ghcr.io/armpro24-blip/aieng-workbench:latest
```

如需让 Agent 引导首次启动,请把
[one-prompt setup](docs/one-prompt-agent-setup.md) 粘贴到支持 MCP 的客户端中。
它会先检查 Docker、后端健康、MCP endpoint、onboarding 调用和审批入口,
再进入可选的 CAD smoke。

alpha 镜像会在 Docker smoke 通过后从 `main` 发布到 GHCR
(`latest` + 不可变的 `sha-<commit>` 标签)。这是 alpha 范围能力,
不是生产认证。

**发布版本:** 当前预发布为
[`v0.1.0-alpha.4`](https://github.com/armpro24-blip/cad-cae-copilot/releases/tag/v0.1.0-alpha.4)。
分发渠道是 Git tag 加 GHCR 镜像 —— **不计划发布到 PyPI**,所以下面的 Git 形式
就是正式安装路径,不是临时替代。只想运行 MCP 服务器、不克隆仓库
(Python 3.11+,固定到发布 tag):

```bash
uvx \
  --from "aieng-workbench-mcp[full] @ git+https://github.com/armpro24-blip/cad-cae-copilot.git@v0.1.0-alpha.4#subdirectory=aieng-ui/backend" \
  --with "aieng-format @ git+https://github.com/armpro24-blip/cad-cae-copilot.git@v0.1.0-alpha.4#subdirectory=aieng" \
  aieng-workbench-mcp --approval-mode client --data-dir ~/.aieng-workbench
```

### 首次成功运行后应该看到什么

第一次 CAD smoke 成功后,你应该能验证:

- 工作台查看器中出现生成的模型,
- STEP/STL/GLB 或 preview artifacts 已写入,
- 生成源码和元数据保存在项目包中,
- 当模型暴露这些信息时,能看到命名零件和可编辑参数,
- 当拓扑映射成功时,能看到稳定的 `@face:*` 引用,
- 确定性 critique 或几何检查会如实报告发现,
- `.aieng` 包中包含可复现 artifacts 和 provenance。

CAE setup、mesh generation 和 solver execution 是单独的审批门步骤。
readiness 或 preflight 报告不代表 solver 已经运行。

**贡献者路径 —— 从源码本地构建**(Docker Compose 或手动方式,用于开发镜像
或运行未合并分支):

```bash
docker compose up -d
# 或:
docker build -t aieng/workbench:local .
docker run --rm -it -p 8000:8000 -p 8765:8765 -v aieng-data:/data aieng/workbench:local
```

在 http://localhost:8000/app/ 打开查看器,将支持 MCP-over-HTTP 的客户端指向
`http://localhost:8765/sse`。项目和 `.aieng` 包保存在 `aieng-data` 卷中。
容器默认启用 `AIENG_MCP_MANAGED_APPROVAL=1`,受控 CAD/CAE 工具通过工作台 UI 呈现。

### 选项 3:本地开发者安装

适合你打算改代码时使用。前提条件:一个**恰好命名为 `aieng311`** 的 conda
环境(Python ≥ 3.11)并安装了 **build123d** —— MCP 配置和运行脚本假设此名称。
`build123d` / OpenCASCADE(OCP)的安装在某些平台上可能很慢或失败;若如此,
请优先使用上面的 Docker 路径。

```bash
conda create -n aieng311 python=3.11 -y
conda activate aieng311
pip install build123d
cd aieng-ui/backend && pip install -e .
```

然后在一个终端中同时启动两个服务(Ctrl+C 停止两者):

```bash
make dev                  # macOS / Linux / WSL
.\dev.ps1                 # Windows PowerShell
python scripts/dev.py     # 跨平台备用
```

后端 → FastAPI 在 `http://127.0.0.1:8000`;前端 → Vite 在
`http://localhost:5173`。自定义端口:`BACKEND_PORT=8080 FRONTEND_PORT=3000 make dev`。

<details>
<summary>单独启动服务 / 运行测试</summary>

```bash
make backend     # 或:cd aieng-ui/backend && uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
make frontend    # 或:cd aieng-ui/frontend && npm install && npm run dev
cd aieng-ui/backend && python -m pytest    # 后端测试套件
```

</details>

## 从规格到经过验证的 CAD

上方的示例模型来自一份明确的工业夹具规格 —— 固定尺寸、命名零件、精确的孔和
槽位置、必需的对称性,且不允许自行添加额外几何体。Agent 执行并验证规格;
不会默默编造工程需求。

<details>
<summary><strong>复制电机安装夹具提示词</strong></summary>

```text
Create a fully specified industrial motor mounting fixture using millimeters.

Coordinate system:
- X is the fixture width, Y is the fixture depth, and Z is vertical.
- Center the complete fixture on X=0.
- Place the bottom face of the base plate at Z=0.

Base plate:
- Create a 180 × 140 × 14 mm base plate.
- Add four Ø11 mm vertical through-holes at X=±70 mm and Y=±50 mm.
- Add an Ø18 mm, 5 mm deep counterbore to the top of every mounting hole.
- Add a 3 mm fillet to the four outside vertical corners.

Motor support:
- Add a centered rear vertical support plate, 130 mm wide, 14 mm thick,
  and 120 mm tall above the base.
- Add a Ø72 mm horizontal locating bore through the plate along Y.
- Position its center at X=0 and Z=78 mm.
- Add four Ø8.5 mm horizontal mounting holes on a Ø100 mm bolt circle.

Reinforcement and rails:
- Add two mirrored 12 mm thick triangular gussets extending 45 mm forward
  and rising 65 mm above the base.
- Add two separate 110 × 12 × 8 mm guide rails centered at X=±38 mm, Y=-12 mm.
- Add one centered 70 × 6 mm longitudinal slot to each rail.

Modeling requirements:
- Create named parts "fixture_body", "guide_rail_left", and "guide_rail_right".
- Color the fixture body dark blue-gray and both guide rails orange.
- Declare all major dimensions as editable UPPER_SNAKE_CASE constants.
- Preserve exact left/right symmetry.
- Verify overall dimensions, named parts, and stable topology pointers.
- Run the deterministic engineering critique after modeling.
- Do not add a motor, fasteners, logos, decorative features, or unspecified geometry.
```

</details>

## 工业 CAD 示例

每个示例都从明确的尺寸、特征位置和建模约束出发 —— Agent 执行并验证规格,
而不是编造工程需求。

<table>
  <tr>
    <td width="33%" align="center">
      <a href="docs/assets/images/example1.webp">
        <img src="docs/assets/images/example1.webp" width="100%" alt="aieng 生成并验证全规格机加工轴承支撑支架">
      </a>
      <br>
      <strong>机加工轴承支架</strong>
      <br>
      <sub>基准、轴承孔、安装孔阵列、加强筋、圆角和评审</sub>
    </td>
    <td width="33%" align="center">
      <a href="docs/assets/images/example2.webp">
        <img src="docs/assets/images/example2.webp" width="100%" alt="aieng 生成并审计全规格六口气动歧管">
      </a>
      <br>
      <strong>六口气动歧管</strong>
      <br>
      <sub>精确外形、孔位间距、沉孔和可编辑尺寸</sub>
    </td>
    <td width="33%" align="center">
      <a href="docs/assets/images/example3.webp">
        <img src="docs/assets/images/example3.webp" width="100%" alt="aieng 生成带命名零件和稳定面指针的工业接线盒组件">
      </a>
      <br>
      <strong>工业接线盒</strong>
      <br>
      <sub>命名组件零件、导出产物和稳定面指针</sub>
    </td>
  </tr>
</table>

<details>
<summary><strong>这些示例验证了什么</strong></summary>

- **机加工轴承支撑支架** — 一个具有指定底座外形、水平轴承孔、对称安装孔阵列、
  镜像加强筋、圆角和倒角的可制造实体。工作台捕获并修正了构造错误,
  然后验证了最终基准、拓扑、可编辑参数和工程评审。
- **六口气动歧管** — 一个由规格驱动的歧管,具有精确的 `160 × 50 × 40 mm` 外形、
  六个等距出口、轴向进气口、带沉孔的安装孔、边圆角、开口倒角和可编辑尺寸。
- **工业接线盒组件** — 一个双零件外壳组件,具有命名的底座和盖子实体、
  内部安装凸台、电缆密封套开口、分离的盖子放置位置、生成的 STEP/STL/GLB 产物,
  以及可用于精确后续工作的可选稳定面指针。

</details>

## 为什么选 aieng — 超越 text-to-CAD

大多数 AI CAD 和 text-to-CAD 演示在模型出现时就结束了。aieng 将几何生成视为
**可审查工程工作流**中的一个步骤,围绕自描述的 `.aieng` 包构建:可编辑参数、
稳定拓扑、溯源和完整的 CAD → CAE 路径在出图之后依然保留。

| 能力 | 典型 text-to-CAD 演示 | aieng |
|------|:---------------------:|:-----:|
| 生成真实 CAD 导出(STEP/STL/GLB) | 是 | 是 |
| 执行明确尺寸和基准 | 部分 | 是 |
| 保留可编辑源和参数 | 部分 | 是 |
| 命名零件并暴露稳定拓扑引用 | 很少 | 是 |
| 验证几何体并运行确定性评审 | 很少 | 是 |
| 对每次编辑出 diff(拓扑漂移 + 可制造性) | 否 | 是 |
| 使构建失败的设计规则断言(`require`) | 否 | 是 |
| 对每个结果标注可信度等级(V&V-40) | 否 | 是 |
| 在一个包中保留产物和溯源 | 很少 | 是 |
| 从 CAD 继续进入 CAE 工作流 | 很少 | 是 |
| 对受控工程操作要求审批 | 很少 | 是 |
| 标准件库 | 否 | 是 |
| 扩展材料数据库(51 种材料) | 否 | 是 |
| BOM 生成 | 否 | 是 |

这能带来什么:

- **真实、可导出的 CAD** — Agent 编写的 build123d / OpenCASCADE 几何体生成
  STEP、STL、GLB、拓扑图、特征图和四视图缩略图。不是桩(stub)。
- **规格驱动执行** — Agent 遵循明确的尺寸、基准、特征位置、对称性和制造约束,
  而不是自由发挥设计。
- **检查并修正** — 几何报告、确定性评审、命名零件和稳定的 `@face:*` 指针支持
  精确验证和后续编辑。
- **可复现工程包** — `.aieng` 包保存几何体、生成的源、分析状态、产物、元数据
  和溯源,让结果可审查而非不透明。
- **Agent 无关的 MCP 工具** — Claude Code、GitHub Copilot、OpenAI Codex、Cursor
  和其他支持 MCP 的 Agent 驱动同一个后端。
- **CAD → CAE 路径** — 材料、边界条件、网格、求解器运行、结果映射和证据与
  CAD 模型共存。

**适合谁:** 想要超越文本和代码生成的工程工具的 AI Agent / MCP 开发者;
探索具有真实几何体的 AI 辅助 CAD/CAE 的机械工程师;以及对 CAD、CAE、MCP、
VS Code 扩展或 build123d / OpenCASCADE 感兴趣的创客、研究人员和开源贡献者。

## 不是只看截图

每一次成功的 CAD 运行都可以保存可检查的工程产物,而不只是渲染图:

- 生成的 build123d 源码,
- STEP/STL/GLB 导出,
- 命名零件和可编辑参数,
- 拓扑映射,以及映射成功时稳定的 `@face:*` 指针,
- 预览缩略图,
- critique 和几何检查结果,
- `.aieng` 证据包中的 provenance 与包元数据。

这意味着另一位工程师或支持 MCP 的 Agent 可以检查、修改、重跑并审阅结果,
而不是只能相信一张静态渲染图。

## 信任层 —— 由构造保证可验证、可解释

拥挤的赛道是*生成*;空白的赛道是*信任*。这里每一个 AI 建议的变更都是经过检查、
可解释的,而不仅仅是渲染出来:

- **每次编辑都出 diff** —— 每次参数编辑 / 替换零件 / 追加都返回 `regression_diff`
  (是否有非目标零件被改动?)和 `critique_diff`(编辑是否让最小壁厚 / 孔间距 /
  悬空件 / 对称性等规则变差?),并在查看器中以 before→after 形式呈现。
- **设计规则断言** —— 在 build123d 代码里写 `require(WALL >= 3, "壁厚低于 3mm")`
  (或裸 `assert`);违规会确定性地使构建失败并返回结构化的 `design_rule_violation`
  —— 约束由构造保证,而非靠期望。
- **V&V-40 可信度分级** —— 每个产出结果都带一个有序等级
  (`critique_finding` < `surrogate_prediction` < `proxy_assembly_result` <
  `executed_solver_result`);声明的可信度永远不超过其证据,且从不假定
  `production_ready`。跑完求解器还不够:在实测精度带为 `unreliable` 的网格上
  完成的求解会被标为 `unreliable_mesh`,而不是 `executed_solver_result`。
- **网格精度靠测量,不靠期望** —— FE 网格默认二阶四面体(C3D10;标定中
  线性四面体把弯曲应力低估约 2 倍),每个网格都带实测 `accuracy` 判定
  (reliable / marginal / unreliable),并可选 GCI/Richardson 收敛性研究
  (`cae.mesh_convergence`)。
- **代理模型误差带纪律** —— 代理模型预测的数值从不脱离其不确定度带 + 留一法验证误差
  单独呈现。
- **一致性门控的自主性** —— 当 LLM 判断步骤的多次采样一致性低时,转为*询问用户*
  而非凭猜测行动。
- **NAFEMS 风格的 V&V 套件** —— 以解析解支撑的线性静力基准在 CI 中守护求解器路径
  (结果是*在容差内对照参考验证*,绝非"认证")。

## 工作原理

1. 提供一份具有明确尺寸和约束的机械规格。
2. 支持 MCP 的 Agent 使用 aieng 工具创建真实 CAD 几何体。
3. aieng 导出模型并记录命名零件、拓扑、可编辑参数、源和溯源。
4. 通过视觉和数值检查结果,然后引用精确的零件、特征或面(`@face:*`)进行后续更改。
5. 查询扩展材料数据库(51 种工程材料),为零件分配准确的力学和热学属性。
6. 将标准件 —— 紧固件、轴承、轴、结构型材和标准孔 —— 直接从库插入模型。
7. 从装配零件生成物料清单(BOM),用于审查和采购。
8. 当所需工程输入可用时,继续进入 CAE 设置和求解器工作流。

**材料与标准件工作流:**
```
aieng.list_materials { category: "Aluminum Alloy" }
aieng.get_material_details { material_name: "Al6061-T6" }
aieng.compare_materials { material_names: ["Al6061-T6", "Steel-316L"] }

aieng.list_standard_parts { category: "fastener" }
aieng.get_standard_part_specs { part_type: "hex_bolt", preset_name: "M8" }
aieng.insert_standard_part { part_type: "hex_bolt", preset_name: "M8", position: [0,0,0] }

aieng.generate_bom { format: "markdown" }
```

工作台 UI 和 [`aieng-vscode-extension`](aieng-vscode-extension) 为实时后端项目和
`.aieng` 包提供可视化检查。

## 在 VS Code 中可视化检查

VS Code 扩展是体验 aieng 最直观的方式 —— 它是 `.aieng` 包格式、MCP 工具和
CAD/CAE 后端的前端,将 AI-CAD 设计循环直接带入编辑器。它可以:

- 以只读自定义编辑器打开本地 `.aieng` 包,
- 连接到实时后端项目预览,
- 可视化生成的 GLB/STL CAD 输出,
- 并将稳定的 `@face:id` 指针复制回与 Agent 的聊天中。

扩展是系统的一层,而非整个系统 —— 核心是让 Agent 和人类共享可复现 CAD/CAE
项目状态的包格式和工程后端。设置和开发说明位于
[`aieng-vscode-extension/README.md`](aieng-vscode-extension/README.md)。
当前 editor-first 路径见
[`docs/mcp-first-vscode-workflow.md`](docs/mcp-first-vscode-workflow.md)。

## 通过 MCP 从 AI Agent 驱动 aieng

后端将其工具注册表暴露为 **MCP 服务器**(`aieng-workbench`),因此 Agent 通过
自己的工具驱动工作台 —— 我们这边不需要 API 密钥。连接配置已提交并在全新克隆时
自动加载,假设 `aieng311` 环境存在:

| Agent | 配置文件 |
|-------|---------|
| Claude Code | `.mcp.json` |
| VS Code / GitHub Copilot | `.vscode/mcp.json` |
| Cursor | `.cursor/mcp.json` |
| Cline | 它自己的 `cline_mcp_settings.json`(从 MCP_SETUP 复制配置块) |
| OpenAI Codex | 在 `~/.codex/config.toml` 中添加 `[mcp_servers.*]`(见 MCP_SETUP) |

**审批有三种方式**(`--approval-mode`):客户端自身的提示(`client`)、工作台
查看器 / VS Code 扩展卡片(`managed`),或为无界面 Agent 提供的**无头 MCP
elicitation**(`elicit`)—— 当无界面可应答时安全地拒绝。运行
`aieng-workbench-mcp --doctor` 可在开始前检查 MCP 配置、后端与工具集是否就绪。
完整的"已测试 vs 已记录"客户端矩阵见
[MCP 客户端兼容性](aieng-ui/backend/docs/mcp_client_compatibility.md)。

**每次会话的前三个调用:**
```
1. aieng.agent_readme                  -> 紧凑的操作入门指南
2. aieng.list_projects                 -> 发现项目 ID
3. aieng.agent_context { project_id }  -> 几何状态、指针、后续步骤
```

使用 `aieng.guide { topic }` 获取任务特定详细信息,或在真正需要完整规范
[`AGENTS.md`](AGENTS.md) 时使用 `aieng.agent_readme { detail: "full" }`。

**可持续的建模循环:**
```
cad.get_source            -> 查看累积的源、命名零件、has_base
cad.execute_build123d     -> 构建/扩展几何体(mode=replace|append)
                            - 在零件上设置 .label -> 可以引用的语义名称
                            - mode=append 在 `previous_result` 上构建
                            - 返回缩略图 + named_parts / parts_added
(检查结果,重复)
```

完整的工具详情、指针语法和审批控制操作见 [AGENTS.md](AGENTS.md);
按客户端的 MCP 连接配置见
[aieng-ui/backend/MCP_SETUP.md](aieng-ui/backend/MCP_SETUP.md)。

## 展示示例

规范后端演示,每个都可作为单个测试运行:

### 1. CAD 生成 → 结构 FEA → 拓扑优化

运行 CAD → FEA → 拓扑优化循环并写回可编辑的优化几何体。

<img src="docs/assets/showcase/geometry_cae_flow.svg" width="800" alt="CAD 生成到结构 FEA 到拓扑优化">

```bash
pytest aieng/tests/test_topology_optimization.py -q
```

**关键产物:** `analysis/topology_optimization.json`, `geometry/shape_ir.json`
**边界:** 2D 平面应力;3D SIMP 仅为实验/参考。
[详情 →](aieng/docs/showcase_gallery.md)

### 2. 从网格重建 CAD → 导出 STEP

从网格重建解析 CAD 并在壳体验证时导出 STEP。

<img src="docs/assets/showcase/mesh_to_cad_flow.svg" width="880" alt="网格到区域分割到曲面拟合到面生成到缝合壳到导出 STEP">

```bash
pytest aieng/tests/test_mesh_brep_solidification.py -q
```

**关键产物:** `geometry/reconstructed.step`(有效时),
`graph/mesh_brep_stitching_plan.json`
**边界:** 网格派生/有损;以平面/圆柱为主;自由曲面/NURBS 为未来工作;
部分壳不产生 STEP。
[详情 →](aieng/docs/showcase_gallery.md)

### 3. 组件模型 → 选定零件优化

构建代理组件分析模型并优化一个选定的设计零件。

<img src="docs/assets/showcase/assembly_optimization_flow.svg" width="880" alt="组件模型到解析接口到简化分析到拓扑优化问题到优化零件">

```bash
pytest aieng-ui/backend/tests/test_assembly_topopt_demo.py -q
```

**关键产物:** `analysis/assembly_topology_optimization.json`,
`parts/bracket/geometry/optimized_shape_ir.json`
**边界:** 仅代理连接;无真实接触/摩擦/螺栓预紧;仅一个设计零件;未经生产认证。
[详情 →](aieng/docs/showcase_gallery.md)

### 4. 设计研究:可调尺寸 → 比较 → 采纳

验证、执行、比较和可选地采纳参数化设计候选,而不覆盖基线。

<img src="docs/assets/showcase/design_study_flow.svg" width="960" alt="设置问题到提出候选到安全检查到构建设计副本到比较选项到采纳最佳">

```bash
pytest aieng-ui/backend/tests/test_design_study_demo.py -q
```

**关键产物:** `analysis/design_study_candidate_ranking.json`,
`analysis/design_study_acceptance.json`,
`accepted/candidate_good/geometry/shape_ir.json`
**边界:** 演示中使用静态指标;无自主优化;无基线覆盖;排名仅供参考。
[详情 →](aieng/docs/showcase_gallery.md)

### 5. 真实 CAD->CAE 价值演示

将一个规范的单体悬臂梁依次经过 CAD 创建、面指针选择、审批门控的 CalculiX
执行、GCI/Richardson 网格收敛性研究、FRD 派生的查看器场结果,以及可分享的
工程报告。

```bash
python aieng-ui/backend/scripts/value_demo_packet.py --format markdown
```

**关键产物:** `simulation/runs/value_demo_run_001/outputs/result.frd`,
`results/computed_metrics.json`, `analysis/mesh_convergence_report.json`,
工程报告 HTML。
**边界:** 需要真实 Gmsh/CalculiX;线性静力 —— 离散化不确定度由 GCI 研究给出界
(参考悬臂梁上实测 1.31%),而非假装不存在;合成 fallback 不算成功演示。
[运行手册 ->](docs/cad-cae-value-demo.md)

## 当前限制

诚实边界 —— 输出是审查材料,而非生产签发:

- 未经生产认证 CAD/CAE。输出仍需人类工程判断。
- 组件接触和螺栓预紧仅为代理;真实非线性接触为未来工作。
- 3D SIMP 为实验/参考,未经生产认证。
- 网格到 CAD 最适用于平面/圆柱主导几何体;更广泛的自由曲面和 NURBS 拟合为未来工作。
- 设计研究是 Agent 引导的显式执行,而非自主全局优化。

## 仓库结构

| 路径 | 状态 | 内容 |
|------|------|------|
| **`aieng-ui/`** | **活跃** | FastAPI 后端、React 工作台和 MCP 服务器 —— 活跃的 CAD/CAE 引擎(build123d) |
| `aieng/` | 核心库 | `.aieng` 语义包格式引擎、模式、验证、CLI、Shape IR 和证据模型 |
| `aieng-vscode-extension/` | 活跃 | VS Code 可视化前端,用于 `.aieng` 包和实时项目预览 |
| `aieng-agent-skills/` | 活跃 | `SKILL.md` 合约,教导 Agent 如何使用生态系统 |
| `legacy/aieng-freecad-mcp/` | 遗留 | 旧的 FreeCAD 执行适配器 —— 活跃路径不使用 |
| `archive/CAD-Agent-main/` | 归档 | 历史和实验性辅助 CAD-Agent 材料 |

## 文档

| 文档 | 用途 |
|-----|------|
| [AGENTS.md](AGENTS.md) | 规范 Agent 指南 —— 工具、工作流和约定 |
| [docs/prompt-guide.md](docs/prompt-guide.md) | 直接对连接的 Agent 说的自然语言句式 —— 面向用户的提示词指南 |
| [aieng-ui/backend/MCP_SETUP.md](aieng-ui/backend/MCP_SETUP.md) | Claude Code、Copilot、Cursor 和 Codex 的每个 Agent MCP 连接配置 |
| [docs/cad_execution_boundary.md](docs/cad_execution_boundary.md) | 本地优先执行边界、隐私姿态、审批门与沙箱限制 |
| [docs/parametric-edit-governance.md](docs/parametric-edit-governance.md) | 可审查、可回退的 CAD 参数编辑工作流与诚实边界 |
| [docs/cae-credibility-ladder.md](docs/cae-credibility-ladder.md) | CAE 证据分级 —— 从产物存在到基准标定与评审支持 |
| [docs/canonical_engineering_scenarios.md](docs/canonical_engineering_scenarios.md) | 规范工程场景 —— 可复现包、验证命令、产物与诚实边界 |
| [aieng-vscode-extension/README.md](aieng-vscode-extension/README.md) | VS Code 扩展使用和开发说明 |
| [aieng/docs/showcase_gallery.md](aieng/docs/showcase_gallery.md) | 展示画廊 —— 演示要点、视觉指导和诚实边界 |
| [aieng/docs/demo_catalog.md](aieng/docs/demo_catalog.md) | 后端演示目录 —— 运行命令、预期产物和成熟度级别 |
| [aieng/docs/backend_capability_matrix.md](aieng/docs/backend_capability_matrix.md) | 能力状态快照 |
| [aieng/docs/roadmap.md](aieng/docs/roadmap.md) | 分阶段开发路线图 |
| [CLAUDE.md](CLAUDE.md) | Claude Code 入口指针 |
| [.github/copilot-instructions.md](.github/copilot-instructions.md) | GitHub Copilot 入口指针 |

## 贡献

欢迎对包格式、后端工作流、MCP 工具链和 VS Code 前端做出贡献。
改善可复现性、可视化检查、工程诚实边界或 Agent 可用性的工作特别符合范围。

## 备注

- 公开仓库。未提交任何密钥;运行时数据(`data/projects/`)、虚拟环境、
  `node_modules` 和嵌入式 conda 环境已加入 gitignore。
- 如果您的 CAD 环境不命名为 `aieng311`,请编辑 MCP 配置中的 `-n aieng311` 参数
  或直接将 `command` 指向您的解释器 —— 见
  [aieng-ui/backend/MCP_SETUP.md](aieng-ui/backend/MCP_SETUP.md)。
- `http://127.0.0.1:8000` 上运行的后端在 Agent 驱动构建时启用实时 UI 更新;
  如果它关闭,MCP 服务器会回退到进程内执行。
