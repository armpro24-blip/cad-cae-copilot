# Branch rulesets (configuration as code)

GitHub rulesets are repository *settings*, not repository *contents* — they do
not apply themselves from this directory. `main.json` is the checked-in source
of truth so the protection posture is reviewable and diffable; applying it is an
explicit action.

## Apply

Currently applied as ruleset **`19975288`**
([settings](https://github.com/armpro24-blip/cad-cae-copilot/rules/19975288)),
`enforcement: active`.

```bash
# update the applied ruleset after editing main.json
gh api --method PUT repos/armpro24-blip/cad-cae-copilot/rulesets/19975288 \
  --input .github/rulesets/main.json

# create (if it was deleted, or on a fork)
gh api --method POST repos/armpro24-blip/cad-cae-copilot/rulesets \
  --input .github/rulesets/main.json
```

Editing `main.json` does **not** change enforcement on its own — run the `PUT`.

## Inspect

```bash
gh api repos/armpro24-blip/cad-cae-copilot/rulesets
gh api repos/armpro24-blip/cad-cae-copilot/rulesets/<id> --jq '.rules'
```

## What `main.json` enforces, and why

| Rule | Rationale |
|---|---|
| Pull request required | Before this ruleset there was no protection at all (`branches/main/protection` → 404, `rulesets` → `[]`), so CI results on `main` were advisory and some commits landed by direct push. Requiring a PR is what gives the status checks somewhere to run. |
| `required_approving_review_count: 0` | Deliberate. This is effectively a single-maintainer repo; requiring one approval would lock the maintainer out of their own PRs, since GitHub does not let you approve your own. The goal here is *checks must be green*, not *a second human must sign off*. Raise this when there is a second reviewer. |
| Block force pushes (`non_fast_forward`) | Keeps `main` history from being rewritten under published tags/releases. |
| Restrict deletions | Self-explanatory for a default branch. |
| `strict_required_status_checks_policy: false` | Does **not** require branches to be up to date with `main` before merging. Strict mode forces a rebase-and-rerun on every intervening merge; not worth the churn at this repo's merge rate. |

## The five required checks

All five run unconditionally on `pull_request: branches: [main]` with **no path
filters**, which is what makes them safe to require:

| Check | Workflow |
|---|---|
| `Backend (focused tests)` | `ci.yml` |
| `Frontend (vitest + build)` | `ci.yml` |
| `Docs / agent onboarding anti-drift` | `ci.yml` |
| `aieng-format installed wheel/sdist smoke` | `packaging-smoke.yml` |
| `aieng-workbench-mcp installed wheel smoke` | `packaging-smoke.yml` |

The two packaging smokes are in the list specifically because they are the
checks that caught the `mcp` 2.0.0 break (see #462/#463) — a dependency shipping
a major version that removed `mcp.server.fastmcp` and left the MCP server, the
product's primary agent-facing surface, unable to import from a clean install.

They keep earning that place after the port (#463): the MCP wheel smoke installs
into a clean venv and resolves `mcp` **unpinned**, so it is the one check that
sees what an external agent actually gets. It now asserts the resolved SDK major
and prints it, so a future break says which side of the rename it landed on.

Context names must match the job `name:` **exactly**. If a job is renamed, this
file and the applied ruleset both need updating, or the required check silently
never reports and every PR blocks.

## Deliberately *not* required yet: `Real CalculiX V&V gate`

The numerical V&V gate (`real-ccx-verification.yml`) is the check that actually
defends solver correctness, and cost is no longer an argument against it — it
completes in about 1m30s on stock `ubuntu-latest`, including
`apt install calculix-ccx`.

It is excluded for a mechanical reason: **it has `paths:` filters.** A required
status check that does not report leaves the PR blocked forever, so requiring a
path-filtered workflow would deadlock every PR that does not touch a solver
path.

Two ways to promote it, once a few more runs confirm stability:

1. **Drop the path filters** so it runs on every PR, then add it here. Simplest,
   and ~90s per PR is affordable.
2. **Keep the filters and add a skip-shim** — a second always-running job with
   the same check name that reports success when the paths did not match. More
   moving parts; only worth it if the runtime becomes significant.

Option 1 is preferred while the runtime stays where it is.

## Escape hatch

No bypass actors are configured (`current_user_can_bypass: never`). If the
ruleset ever blocks legitimate emergency work, disable or delete it rather than
working around it:

```bash
# log-only: rules evaluate and report but do not block
gh api --method PUT repos/armpro24-blip/cad-cae-copilot/rulesets/19975288 \
  -f enforcement=evaluate

# off entirely
gh api --method DELETE repos/armpro24-blip/cad-cae-copilot/rulesets/19975288
```
