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

### Saying where it is held and where the load acts

This is the part engineers say is hard to write down. You do not need face ids
or coordinates — ordinary engineering words work, in Chinese or English:

| You say | It binds |
|---|---|
| 底面固定 / fix the bottom | the downward-facing planar face |
| 螺栓孔固定 / fixed at the bolt holes | the whole hole pattern of one size |
| 肋的顶面加载 / load on `rib_main` top | that part's most upward-facing surface |
| 最大平面 / the largest flat face | the biggest planar face |
| 右侧 / +X side | the face pointing that way |
| `@face:face_005` | exactly that face, no interpretation |

Scoping by part name is what resolves the usual ambiguity: `底面` on a whole
assembly may be several faces, `base_plate 底面` is one.

**It answers back in the same language**, so you can check it before any solving
happens:

```
fixed (all DOF 1-3): @face:face_005  plane  9521.5 mm²  normal=[0,0,-1]  on base_plate
load: 500 N along [0.00, 0.00, -1.00] on @face:face_012  plane  235.8 mm²  on rib_main
material: Al6061-T6 (E=69 GPa, ν=0.33)
```

If your wording could mean two different faces, it **stops and lists them**
rather than picking one — you will see `needs_user_input` with the candidate
faces, their sizes and directions. A sloped face (the top of a triangular
gusset) is accepted but labelled `inclined 32° from top`, so you know it is not
flat-on. A load of 0 N is refused outright: it would solve happily and report
zero stress.

Scope today is **linear static only**. Results are mesh-dependent until you ask
for a convergence study, and a solver run is evidence — not certification. The
workbench states that in its own output; it will not report a solve it did not
actually run.

### Writing it down as a requirement

If the load case is part of the spec — not a one-off question — record it, and
say what the part must survive:

> 记一个工况叫 motor_thrust：底面固定，肋顶面 500N 向下，安全系数不低于 2，
> 位移不超过 0.5mm。

It is checked as you write it: if "底面" could mean two faces, it stores nothing
and shows you the candidates. Later, `跑 motor_thrust 这个工况` applies exactly
what was recorded, and after solving you get each criterion back as
**pass / fail / unknown** — a criterion the run could not measure stays
`unknown` with the reason, never a quiet pass.

The requirement survives rebuilds and geometry changes, so "did this design
still meet the spec?" stays answerable.

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
have — no need to restate the physics.

**It will tell you when your part is the wrong shape for the 2D idealization.**
The 2D plane is spanned by the part's two largest dimensions, so a plate or
bracket loaded in *bending* (the load pressing on its face) has no in-plane
force at all — plane-stress cannot represent that, in any plane. You get a
refusal saying exactly that, pointing at 3D, instead of a plausible-looking
result computed for someone else's load case. In-plane loading (a shear web, a
frame pulled in its own plane) is the 2D path's home ground.

**3D is experimental** and the output is a mesh proxy — usable for shape
insight, not production CAD. The tool labels it that way rather than pretending
otherwise.

The design space defaults to the **largest single solid**. If your load lands on
a different body — a rib on a base plate — the tool says so by name and asks
which design space you meant, rather than optimizing the plate as if the rib
were not there.

## 8. Say how the parts go together

> 这是三个零件：机壳、端盖、轴。端盖用螺栓压在机壳顶缘上，轴装在机壳的轴承孔里。

You describe the joints the way you would to a colleague — which parts, which
faces, and what kind of joint. The workbench binds each interface to real B-Rep
faces and then **checks the joint against the geometry**, so a mistake surfaces
while it is still cheap:

- A tie / weld / bolt whose two faces **do not touch** is refused outright — you
  cannot join across a gap at any scale — and it is left out of the analysis
  model rather than silently transferring load across empty space.
- A shaft-in-bore or gear mesh can be stated as such (`concentric`, `tangent`),
  and a pair whose axes do not actually line up is marked invalid.
- An interface that resolves to **no faces** blocks the assembly as unsafe to
  solve, and one that covers essentially the whole part is flagged as
  over-broad — tying down more of the part than really mates over-constrains it.

Ask *"这个装配现在有什么问题?"* at any point and you get the refused joints and
blocking interfaces by name.

**The honest boundary here is wider than elsewhere.** Assembly connections are
**simplified proxies**: no nonlinear contact, no bolt preload, no friction. What
the workbench verifies is that the joints are geometrically possible and
correctly bound — not that the assembly's stresses are right. Treat an assembly
result as a layout and load-path check, not as a qualified analysis.

---

## What the agent will always do

- **Ask before it changes geometry** (once per modeling plan, not per step) and
  **ask again before running the solver**.
- **Refuse to invent** a face, a part, or a result it does not have — it will
  say "not found" or "no solver was executed" instead.
- **Tell you the evidence level** of any number it reports, so a mesh proxy or
  an unverified estimate is never presented as a solved result.
- **Measure mesh accuracy instead of hoping** — every mesh carries a measured
  accuracy verdict, and a solve on an unreliable mesh is downgraded
  (`unreliable_mesh`), not reported as a result.

## What it is not

Linear static analysis only, no fatigue / buckling / contact / bolt preload.
Heuristic manufacturability rules, not certification. Every output is review
material for a qualified engineer — the workbench is explicit about this in its
own responses, and so should you be when passing results on.
