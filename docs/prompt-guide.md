# What to say — driving the workbench by prompt

You do not learn a tool vocabulary. You describe what you want in a normal
sentence to a connected agent (Claude Code, Codex, Cursor, …) and it picks the
tools. This page lists sentences that work, in the order a real job runs.

Setup is separate: see [`MCP_SETUP.md`](../aieng-ui/backend/MCP_SETUP.md). Once
your agent lists `aieng.*` / `cad.*` / `cae.*` tools, everything below applies.

Chinese works as well as English — say it however you normally would.

---

## 1. Make something

> 做一个 CNC 铝合金支架，底板 120×80×8 mm，四个 M6 安装孔，加一条加强筋。

> Model a 100×20×10 mm cantilever beam I can run a static analysis on.

The agent shows you a **modeling plan** first — parts, key dimensions,
assumptions — and waits for your approval before touching geometry. Approve,
revise, or cancel. After that it iterates without re-asking for every step.

You get back a 4-view image plus measured numbers (sizes, proportions, whether
any part is floating or asymmetric), so you can judge it without opening a CAD
seat.

**Say this if it looks wrong:** just describe the problem —
*"筋太薄了"*, *"孔离边太近"*, *"the arm should reach the base plate"*.

## 2. Check it before trusting it

> 这个零件能加工吗？按 CNC 铝的规则检查一下。

> Review this design and tell me what you'd fix first.

Returns a deterministic audit — minimum wall thickness, hole sizes and
edge distances, floating parts, missing mounting interfaces — each finding
naming the rule and threshold it used. It is a heuristic DfM check, not a GD&T
solver, and it says so.

## 3. Change a dimension

> 把壁厚改成 5mm。

> Make the beam 8 mm thick instead of 10.

Fast path: no regeneration, no LLM guessing. You also get a **regression diff**
that says whether only the part you targeted changed — if a shared constant
moved other parts too, it tells you.

**Requires the model's dimensions to be named constants.** Agent-built models
normally are; if the response says there are no editable parameters, ask:

> 把这些尺寸改成可编辑的具名常量，几何不要变。

**Want to see the options first?**

> 这个模型有哪些尺寸是可以直接改的？

## 4. Run a static analysis

> 左端固定，右端加 50N 向下的力，用 6061 铝，跑一个静力学分析。

The agent picks the faces from the live model, sets up material / support /
load, meshes, and **pauses for your approval before the solver runs**. You get
max displacement, max von Mises stress, and where the peak is.

Scope today is **linear static only**. Results are mesh-dependent until you ask
for a convergence study, and a solver run is evidence — not certification. The
workbench states that in its own output; it will not report a solve it did not
actually run.

**Then:**

> 结果怎么样？哪里最危险？

> Generate an engineering report for this.

## 5. Optimize the size

This is the payoff — it solves *every* candidate with the real solver, not a
surrogate:

> 在满足强度的前提下，把这个梁做得更轻一点。

> Sweep the thickness from 6 to 10 mm and find the lightest one that stays
> under half of yield.

You get a ranked table: each thickness with its real stress, displacement, and
mass, marked feasible or not. **The baseline is not modified** unless you say
so:

> 把最优的那个应用上去。

Measured on the reference cantilever (real Gmsh + CalculiX): 6 mm → 40.4 MPa /
mass 12000, 10 mm → 14.5 MPa / mass 20000, winner 6 mm under a 138 MPa
allowable. Both agree with hand calculation to ~3%. A variant that fails to
build or solve is reported honestly and never recommended.

**Two or more dimensions at once:**

> 同时调壁厚和筋高，找最轻的组合。

## 6. Ask whether the number is actually converged

Any single solve is mesh-dependent. Ask for the check in a sentence:

> 这个结果收敛了吗？换几个网格密度验证一下。

> Is this mesh-converged? Run a convergence study.

Solves the same part at several mesh densities and reports a Grid Convergence
Index per metric, plus a Richardson-extrapolated value. On the reference
cantilever: extrapolated **14.875 MPa** against a closed-form **15.00 MPa**, with
a **1.31%** numerical uncertainty band — i.e. converged.

A GCI bounds *discretization* uncertainty only. It does not say the model is
right — wrong loads, wrong supports, or the wrong physics stay wrong at any mesh
density.

## 7. Topology optimization

> 帮我做一个拓扑优化，看看材料该怎么分布。

Derives the design space, supports, and loads from the CAE setup you already
have. 2D is the solid path; **3D is experimental** and the output is a mesh
proxy — usable for shape insight, not production CAD. The tool labels it that
way rather than pretending otherwise.

---

## What the agent will always do

- **Ask before it changes geometry** (once per modeling plan, not per step) and
  **ask again before running the solver**.
- **Refuse to invent** a face, a part, or a result it does not have — it will
  say "not found" or "no solver was executed" instead.
- **Tell you the evidence level** of any number it reports, so a mesh proxy or
  an unverified estimate is never presented as a solved result.

## What it is not

Linear static analysis only, no fatigue / buckling / contact / bolt preload.
Heuristic manufacturability rules, not certification. Every output is review
material for a qualified engineer — the workbench is explicit about this in its
own responses, and so should you be when passing results on.
