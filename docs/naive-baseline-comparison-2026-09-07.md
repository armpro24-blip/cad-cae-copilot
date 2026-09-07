# Naive-baseline comparison — 2026-09-07

The direction review's week 3–4 check, run without external users because three
of its four minimum signals do not need them.

> 核心问题不是"能不能生成 STEP"，而是：**为闭环和证据层付出的复杂度，是否换来
> 更少操作、更少错误或更容易复核？** 如果朴素脚本同样快、同样可靠，优先简化产品，
> 而不是补充宣传词。

Same task, same machine, same model, same mesh size, same edit:

- **Path A** — the workbench, driven through the MCP tools.
- **Path B** — the same libraries (build123d, gmsh, CalculiX) invoked directly
  from one script, results in a plain directory. No `.aieng` package, no face
  pointers, no provenance, no refusals. Imports nothing from `aieng`.

Task: a 120 × 20 × 10 mm cantilever, Al6061-T6, fixed at one end, 500 N down on
the other; solve; double the thickness to 20 mm; re-solve; report the change.
Beam theory says displacement ∝ 1/t³ (−87.5%) and bending stress ∝ 1/t² (−75%).

Both scripts are in the session scratchpad (`naive_baseline.py`,
`workbench_timed.py`); the numbers below are single runs on one Windows machine.

## Result

| | A — workbench | B — naive scripts |
|---|---|---|
| max displacement | **−87.2%** | **−87.2%** |
| max von Mises | **−74.5%** | **−74.5%** |
| wall clock | 14.4 s | **4.3 s** |
| steps the user drives | 15 tool calls | 1 script run |
| author burden | — | ~200 lines, written once |
| **injected failure caught** | **yes** — `stale_deck`, in 0.01 s | **no** — reported 0.0% change |
| provenance after the fact | run ids + geometry revisions 0 → 1 | none |

### On a clean run, the evidence layer buys nothing

The two paths return the *same numbers*, both matching beam theory. That should
be said plainly: the product's value is not that it computes better. A competent
script computes exactly as well.

### Path B is 3.3× faster, and the overhead is the artifact layer

A's two slowest calls are `cad.execute_build123d` (6.6 s) and
`cad.edit_parameter` (2.7 s) — package writing, topology extraction, stable face
ids, previews, thumbnails. The solves themselves are 1.8 s and 2.3 s, comparable
to B. So the 10 s gap is almost entirely the evidence layer, which is precisely
what is under review.

The wall clock also flatters B, and by a lot: **4.3 s excludes writing the
script.** That script had to know that gmsh's 10-node tetrahedron ordering
differs from CalculiX's C3D10 in the last two mid-edge nodes — get it wrong and
you get a silently wrong stiffness, with no error. For a *one-off* the authoring
cost dominates and B is far more expensive than 4.3 s suggests. By the tenth
part B's script is amortised and A's per-run overhead is the whole cost. That is
the same axis as the review's "someone comes back with a second part" signal.

### The injected failure is the entire difference

After the edit, both paths were asked to re-run the **pre-edit deck** — a
plausible slip, since the earlier deck is still on disk and still solves.

- **B solved it happily** and reported `max_displacement` and
  `max_von_mises_stress` **identical to the baseline: 0.0% change on a beam
  whose thickness had doubled.** Nothing in the directory knew the geometry had
  moved.
- **A refused** in 0.01 s with `code: "stale_deck"`.

This is the same defect that was found and fixed *inside* the workbench (#532).
Reproducing it in the naive path shows it is not an artefact of the workbench's
complexity — it is what iterating on a part does to anyone, and the guard is
what the closed loop is actually for.

### Path B is *immune* to a problem Path A spends heavy machinery on

Worth stating because it cuts the other way. B selects its node sets **by
coordinate** (`abs(x) < tol` for the fix, `abs(x - L) < tol` for the load) —
which is what someone scripting by hand naturally writes. A coordinate
predicate re-evaluates against whatever geometry is current, so it does not care
that OCCT renumbered the faces.

Path A instead binds to `@face:` pointers, and the apparatus that keeps those
working across an edit — stable face ids, `face_signatures` re-verification,
recorded selectors, deterministic re-resolution, rebind reporting — exists to
solve a problem the coordinate predicate does not have. It buys precision a
coordinate band cannot express (a specific bolt hole, an inclined gusset face)
and a vocabulary a user can speak. It costs an entire failure class.

## Answering the review's question

| | verdict |
|---|---|
| 更少操作 | **No.** 15 calls and 3.3× the wall clock against one script run. |
| 更少错误 | **Yes, decisively** — but only for errors of *iteration*, not of computation. The stale deck is caught; the arithmetic was never in doubt. |
| 更容易复核 | **Yes.** B leaves a directory that cannot say which number came from which geometry. A answers it from the package alone, after export. |

## What follows from this

The value is concentrated in the **iteration and review** layer, not in
execution. Two consequences worth acting on:

1. **Do not buy breadth.** More analysis types or CAD adapters add execution
   surface, which is the half where a plain script already ties. The review's
   pause list was right.
2. **The review's SDK/MCP fallback deserves to stay open.** If the evidence
   layer is the value and the workbench UI is the cost, then "证据层受欢迎但 UI
   不受欢迎，就转 SDK/MCP runtime" is not a retreat — this measurement is mild
   evidence for it, since everything measured here was reached through the MCP
   tools with no UI involved.

## Limits of this measurement

- **n = 1**, one machine, one part. The 3× wall-clock gap is large enough to be
  real; do not quote the seconds as precise.
- **The same agent wrote both paths**, and it knows the workbench well. Path B
  is the *favourable* case for a naive script: expert-written, no fumbling.
- **One injected failure.** The stale deck is the one the workbench guards best.
  A fair extension would inject failures the workbench does *not* guard — a
  multi-face `"bolt holes"` selector after an edit is currently refused outright
  (see the part-family sweep), which a coordinate predicate would have survived.
- **Install time is not measured here** and the review asks for it separately.
