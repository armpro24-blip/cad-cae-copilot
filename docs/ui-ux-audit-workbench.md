# AIENG Workbench — UI/UX Audit

Audit date: 2026-07-01
Scope: the live web workbench (`aieng-ui/frontend`) after PR #454–#457.
Method: live inspection at 1600×1000 against the running app (`:5173` + backend
`:8000`), across the representative project states below. This is an
audit/report — no product code was changed.

Screenshots captured during the audit (session scratchpad):
`before-solved`, `after-solved-aligned`, `after-model-ready`, `after-warning`,
`audit-advanced-details`, `audit-commands`, `audit-settings`, `audit-report`.

Representative projects used:
- Empty: `STEP workbench project` (`14fa7590eb20`)
- Model-ready / setup-needed: `Gearbox (designed)` (`937ad6191bd7`),
  `CNC Bracket — M6 Mount` (`4bff622daa1e`)
- Solved / results + report-not-ready: `FEA Validation Cantilever` (`6bd4cbe32c00`)
- Evidence-warning: `CNC Bracket — M6 Mount` (`4bff622daa1e`)

---

## 1. Executive summary

### Current state after #454–#457
The visual foundation is solid and genuinely reads as a research-grade
engineering tool rather than a debug console:

- **Primary CTAs** are strong blue with near-white text and a clear
  primary/secondary/tertiary hierarchy (#454).
- **Focus rings** and keyboard affordances exist app-wide (#454).
- The right **inspector** leads with a dominant, full-width **"Project status"**
  card: a plain-language lifecycle checklist (Model → Simulation setup → Solver
  result → Computed metrics → Engineering report) + one recommended next action,
  with the technical catalogue tucked under **Advanced details** (#447, #457).
- Secondary panels are **collapsible and calm** by default (#444).
- **Warnings** are calmer amber cautions, red reserved for real blockers (#455).
- **Dark surfaces** were consolidated to fewer nested tones (#456).
- The **credibility/honesty** model (executed-solver vs unverified, "not
  modeled" caveats) is a real differentiator and is surfaced honestly in the
  results hero.

### Overall strengths
1. The "what's done / what's missing / what's next" question is answerable in
   ~5 seconds from the Project status card.
2. Trust is explicit: metrics carry a credibility tier and honesty boundaries.
3. Density is controllable — the user opens only the panels they need.
4. Button and text hierarchy are now consistent.

### Main remaining UX risks
1. **Competing / contradictory status signals.** The internal **"Value demo
   check"** panel shows `demo blocked · missing simulation/setup.yaml` even on
   the fully-solved cantilever, directly contradicting the Project-status card
   ("READY", all core steps ✓). This is the single biggest first-time-user
   confusion risk.
2. **Cross-surface trust inconsistency.** The workbench labels the solved result
   `Executed-solver result`; the generated **Report** labels the same result
   `Credibility: unknown / Overall unknown`. Same run, two trust stories.
3. **Duplicated status.** The topbar chips (`PACKAGE / APPROVALS / NEXT`)
   restate the inspector's package status, approval state, and recommended next
   action — the same information competes in two places.
4. **Internal vocabulary leaks** ("Value demo check", "package evidence",
   trust-badge phrases, `Settings` → drawer titled `Environment`).
5. **Coarse sidebar status** — every built project reads "Model Ready" whether it
   is a bare model or fully solved, so lifecycle progress is not scannable.

None of these are visual-polish problems; they are product-level clarity issues.

---

## 2. State-by-state review

### 2.1 Empty project / no geometry
- **Understands:** This is a guided start; a welcome card explains the 3-step
  CAD → CAE → results flow with copyable example `/commands`, and the sidebar
  offers "Import STEP".
- **May confuse:** Once "Got it" is dismissed there is no persistent reminder
  that the workbench is agent-driven (you type `/commands` to your connected
  agent; the GUI doesn't act directly). A first-timer who imports nothing and
  dismisses the card is left on an empty grid. The left sidebar also has a large
  empty vertical gap between "New project" and the import area.
- **Primary action:** "Import STEP" (sidebar) or copy a `/build` command.
- **Visually clear?** Mostly. The welcome card is the clear anchor; the two
  sidebar affordances are secondary and quieter.
- **Wording/hierarchy:** Good. The example prompts are readable in mono.

### 2.2 Model-ready project (Gearbox)
- **Understands:** "CAD evidence loaded; CAE setup not complete." Model ✓;
  Simulation setup / Solver result / Computed metrics / Engineering report shown
  as not-done/locked with plain consequences. Recommended next step: "Define CAE
  setup" → Copy prompt.
- **May confuse:** Below the status card, **Value demo check** shows
  `demo blocked` and **Engineering critique** shows a finding badge — a
  model-ready project already displays two diagnostic panels that can read as
  "problems" before the user has done anything wrong.
- **Primary action:** "Define CAE setup" (Copy prompt). Clear.
- **Visually clear?** Yes for the primary card; the diagnostic panels slightly
  dilute focus.
- **Wording:** "Define CAE setup" is good, engineering-appropriate language.

### 2.3 Setup-needed / evidence-warning (CNC Bracket)
- **Understands:** "CAE setup in progress; evidence gaps remain." Simulation
  setup is an **amber caution**: "Required input still missing: loads — the
  simulation cannot run until these are set." Next step: "Fill required CAE
  inputs" → Copy prompt. This is excellent — a specific, consequence-worded gap
  and a clear next action.
- **May confuse:** The **Value demo check** panel restates the gap in raw terms
  (`missing simulation/setup.yaml`) with its own `demo blocked` badge — a second,
  more technical warning for the same underlying state.
- **Primary action:** "Fill required CAE inputs." Clear.
- **Visually clear?** Yes. Amber caution severity is well-judged (not alarming).
- **Wording:** Strong. The only weak spot is the parallel raw-path warning.

### 2.4 Solved / results project (Cantilever)
- **Understands:** Results hero shows `MAX VON MISES 8.470 MPa`,
  `MAX DISPLACEMENT 0.07464 mm`, `Executed-solver result`, and honest "NOT
  MODELED" caveats. Project status: 4 steps ✓, Engineering report not yet
  generated. Next step: **Generate report** (real blue primary action) with
  secondary "Export evidence packet" and "Copy agent prompt".
- **May confuse:** **Value demo check** still says `demo blocked · missing
  simulation/setup.yaml` on this fully-solved project — flatly contradicting the
  READY status and the executed result. The viewer field legend also shows "Von
  Mises / No solver result for this field" in the corner while the hero shows a
  real Von Mises result — a possible contradictory micro-message worth checking.
- **Primary action:** "Generate report." Visually clear (strong blue).
- **Wording/hierarchy:** Excellent for the hero + status; undermined by the
  contradictory diagnostic panel.

### 2.5 Report view (new tab, backend HTML)
- **Understands:** A clean, printable "Engineering Report" with project meta, a
  4-view thumbnail, an amber **Honesty Boundary** ("review artifact only… does
  not certify design safety"), a **Credibility Stamp**, Bill of Materials, and a
  Key Results table (matching `8.4702 MPa`).
- **May confuse:** (a) It is **light-themed** and opens in a **separate tab**,
  a hard visual break from the dark workbench, with no in-app confirmation that
  the report was produced. (b) The **Credibility Stamp reads `Overall unknown`**
  while the workbench calls the same result `Executed-solver result` — the trust
  language does not match across surfaces.
- **Primary action:** N/A (read-only artifact). Fine.

### 2.6 Evidence packet
- **Understands (partially):** The topbar tooltip now distinguishes it from the
  Report ("raw traceability artifacts… behind the report").
- **May confuse:** Clicking "Export evidence packet" writes markdown + manifest
  **server-side** and toasts a file path. A first-time user does not get a
  downloadable file or an obvious place to find the output; the outcome is
  abstract ("Wrote …/packet.md").
- **Primary action:** Export. The *result* of the action is unclear.

### 2.7 Commands view (drawer)
- **Understands:** Five routed commands — `/build` and `/modify` (CHANGES
  GEOMETRY), `/critique` and `/explain` (READ-ONLY), `/simulate` (SIMULATION) —
  each with a mono example and Copy. Very clear, and the read-only/changes-
  geometry badges set correct expectations.
- **May confuse:** Nothing significant. This is a strong surface.

### 2.8 Settings view (drawer)
- **Understands:** LLM provider config (template, provider, model, API key, base
  URL, sampling params), Local Agent readiness.
- **May confuse:** (a) The nav button says **"Settings"** but the drawer title is
  **"Environment"**. (b) The action row is **Restore defaults / Test config /
  Verify** with **no obvious primary "Save/Apply"** — it is unclear how (or
  whether) edits persist, and all three buttons share the same tertiary weight.
- **Primary action:** Ambiguous.

---

## 3. Navigation and terminology review

| Term | Where | Verdict | Note / risk |
|------|-------|---------|-------------|
| **Project status** | Inspector primary card | ✅ Good | Clear, user-facing. |
| **Mission Control** | Internal name / earlier label | ⚠️ Internal | No longer visible as a title (now "Project status"), but the class/API name lingers; keep the user-facing "Project status". |
| **Value demo check** | Inspector panel | ❌ Internal/leaky | Dogfooding term; its "demo blocked" state contradicts real project status. Should not be a default user-facing panel. |
| **Package evidence / .aieng package** | Advanced details, headline | ⚠️ Technical | Fine inside Advanced details; avoid in top-level guidance. |
| **Evidence Packet** | Topbar, action | ⚠️ Better w/ tooltip | Distinct from Report via tooltip, but the *outcome* (server-side files) is opaque. |
| **Report** | Topbar, action | ✅ Good | Clear; distinction from Packet now tooltip-backed. |
| **Setup / Solve / Results / Model** | Workflow stepper | ✅ Good | Standard CAE vocabulary; reads clearly. |
| **Commands** | Topbar | ✅ Good | Clear once opened; badges help. |
| **Settings** | Topbar → "Environment" drawer | ⚠️ Mismatch | Button label ≠ drawer title. |
| **Executed-solver result / unverified** | Results hero, report | ⚠️ Inconsistent | Same run labeled differently in report vs workbench. |
| **Advanced details** | Inspector disclosure | ✅ Good | Correctly signals "technical, optional". |

Overall: the *user-facing* vocabulary (Project status, Setup/Solve/Results,
Generate report) is good. The leaks are **"Value demo check"**, the
**Settings/Environment** mismatch, and raw file paths surfacing outside Advanced
details.

---

## 4. Inspector panel review

**Ordering (top → bottom, solved project):** Project status → Value demo check →
Last edit → Edit dimensions (+ Engineering critique / timeline when present).

- **Dominant card:** ✅ Project status is full-width, aligned, always-open, with
  the largest title (14px) — correctly dominant after #457.
- **Action area:** ✅ The "RECOMMENDED NEXT STEP" block is an accent-tinted
  sub-panel with one blue primary + quiet secondaries — clear.
- **Checklist readability:** ✅ Icon + title + consequence line per step, with
  done items muted and outstanding/blocked items emphasized. Reads as a
  checklist, not a log.
- **Advanced details:** ✅ Collapsed by default; ⚠️ contents (passport member
  counts, trust badges like "Claim not advanced" / "Results unknown", evidence
  notes) are still developer-flavored — acceptable as "advanced" but the badge
  wording could be softened.
- **Warning severity:** ✅ Amber caution for setup gaps; red reserved. Good.
- **Ordering issue:** ❌ **"Value demo check" sits second**, directly under the
  primary card, and can contradict it. A contradictory diagnostic should not be
  the first thing under the dominant card. Recommended order:
  1. Project status (state + next action) — dominant
  2. Key result metrics (already in the viewer hero; keep)
  3. Edit dimensions (the main user action surface)
  4. Engineering critique (findings, when present)
  5. Timeline / Last edit
  6. Advanced diagnostics (incl. any evidence-completeness gate) — quietest

---

## 5. Prioritized improvement backlog

| # | Title | Problem (user confusion / risk) | Proposed UX improvement | User benefit | Risk | Backend? |
|---|-------|---------------------------------|--------------------------|--------------|------|----------|
| 1 | Stop "Value demo check" contradicting Project status | On a solved project it shows `demo blocked · missing simulation/setup.yaml`, contradicting "READY". First-timers can't tell if the project is done. | Gate it behind a dev/diagnostics flag, or move it into Advanced diagnostics (bottom, quiet) and rename to user-facing "Evidence completeness"; never show `demo blocked` above the status card. | Removes the top contradictory signal; single source of truth for state. | Low | No |
| 2 | Unify credibility label across workbench and report | Report says `Overall unknown` while the workbench says `Executed-solver result` for the same run. Erodes the trust story. | Derive the report credibility stamp from the same classifier as the results hero. | Consistent, defensible trust signal end-to-end. | Med | Yes (report gen) |
| 3 | De-duplicate topbar status chips vs inspector | `PACKAGE / APPROVALS / NEXT` repeat the inspector's package/approval/next-action. Same info competes in two places. | Reduce topbar chips to an at-a-glance summary that is not restated in the inspector, or show them only when the inspector is collapsed/off-screen. | Less competition for attention; clearer focus. | Low–Med | No |
| 4 | Richer sidebar lifecycle status | Every built project reads "Model Ready" — a bare model and a solved project look identical; progress isn't scannable. | Derive Empty / Model ready / Setup ready / Results ready from the project summary (already fetched for the open project) for each list item. | Scan which projects have results at a glance. | Med | Maybe (per-item summary) |
| 5 | Align "Settings" button with "Environment" drawer title | Button label ≠ drawer title; minor disorientation. | Use one term (recommend "Settings" everywhere, or subtitle the drawer "Settings · Environment"). | Consistent navigation vocabulary. | Low | No |
| 6 | Clarify Settings save/apply hierarchy | Restore / Test / Verify with no obvious primary; unclear how edits persist. | Make the persist action a blue primary ("Save"/"Apply"); keep Test/Verify/Restore secondary/tertiary. | User knows how to commit config. | Low–Med | No |
| 7 | Make the evidence-packet outcome tangible | Export writes server-side files and toasts a path; user doesn't get or find an artifact. | State plainly what was produced and where, and/or offer an in-browser download; frame it as "for reviewers". | User understands what they exported. | Med | Maybe (download) |
| 8 | Report continuity with the app | Report is light-themed in a new tab with no in-app confirmation; abrupt break. | Add an in-app "Report generated" confirmation/preview affordance; optionally offer a dark-mode/print toggle. Keep light default for print. | Smoother workflow; user isn't dropped into a disconnected page. | Med | Yes (template) |
| 9 | Soften Advanced-details jargon | Trust badges ("Claim not advanced", "Results unknown") and passport terms are developer-flavored. | Light wording pass to plain engineering language inside Advanced details; keep the honesty semantics. | Advanced view is comprehensible if a user opens it. | Low | No |
| 10 | Persistent "how this works" affordance | The agent-driven ("type /commands to your agent") model is only in the dismissible welcome card. | Add a small persistent hint or a "How this works" link in the empty/idle state. | Correct mental model; fewer "why doesn't clicking do anything" moments. | Low | No |
| 11 | Resolve viewer legend "No solver result" on solved projects | The field legend can show "No solver result for this field" while the hero shows a real field — contradictory micro-copy. | Investigate; align the legend empty-state with actual field availability. | No contradictory viewer text. | Low | Maybe |
| 12 | Single source for "next action" | The next step appears in the topbar NEXT chip, the stepper subtitle, and the inspector action block. | Designate the inspector as the canonical next-action; trim the topbar NEXT to a glance only. | One unambiguous "what to do next". | Low | No |

---

## 6. Recommended next PRs

**PR A — De-duplicate and de-jargon the status surfaces** (frontend-only, low
risk, highest clarity payoff): items **1, 3, 5, 9, 12**. Move/rename "Value demo
check", slim the topbar chips, fix the Settings/Environment label, soften
Advanced-details wording, make the inspector the single next-action source.

**PR B — Sidebar lifecycle + first-run orientation** (low–med): items **4, 10**.
Richer per-project status labels and a persistent "how this works" affordance.

**PR C — Cross-surface trust & report/packet continuity** (medium, backend-
touching, review carefully): items **2, 7, 8, 11**. Unify credibility across
workbench and report, make packet/report outcomes tangible, resolve the viewer
legend contradiction.

**PR D — Settings action hierarchy** (small, standalone): item **6**. Clarify the
primary save/apply action in the Environment drawer.

Suggested order: A → B → D → C (A/B/D are low-risk frontend; C needs backend
coordination and careful review).

---

## 7. Non-goals (do not change in these PRs)

- Engineering calculations, solver behavior, or result semantics.
- Approval flow / gating.
- Evidence, credibility-tier, or claim-boundary **logic** (only the *labeling
  consistency* in item 2 — and only to match the existing classifier, not to
  relax honesty).
- Backend APIs' shape/contracts (items 2/7/8 extend outputs, not restructure).
- The primary-button and surface tokens from #454/#456, or the inspector
  hierarchy from #457.
- Major navigation rewrite (no route changes; only labels/tooltips).
- Full design-system replacement; the dark engineering aesthetic stays.
- Faking any geometry, solver, load, constraint, report, or result state.

---

### Appendix — quick verification notes
- Contradiction reproduced live on `6bd4cbe32c00` (solved): Project status
  "READY" + `Executed-solver result`, while "Value demo check" shows
  `demo blocked · missing simulation/setup.yaml`.
- Report credibility mismatch reproduced at
  `GET /api/projects/6bd4cbe32c00/report` (Credibility Stamp "Overall unknown").
- Settings/Environment mismatch reproduced by opening the Settings button.
- No code changed during this audit.
