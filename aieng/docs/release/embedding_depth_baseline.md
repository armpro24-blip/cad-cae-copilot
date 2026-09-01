# Embedding-Depth Baseline

`strategic_direction_2026.md` §3a names **MCP-server installs**, **`.aieng`
packages created**, and **third-party integrations** as the real success signal.
The alpha release gate read "installs" as PyPI download counts; PyPI is out of
scope by owner decision (#273), so that number will never exist.

This is the replacement: what the channels this project actually uses can and
cannot tell us, captured on a date, with the gaps named. Issue #510.

Recapture with:

```bash
python scripts/capture_embedding_depth_baseline.py             # human table
python scripts/capture_embedding_depth_baseline.py --json      # machine-readable
python scripts/capture_embedding_depth_baseline.py --markdown  # the table below
```

Read-only; needs `gh` authenticated with push access on the repository (the
traffic endpoints require it).

## Baseline — captured 2026-09-01

| Signal | Value | Window | Source |
|---|---:|---|---|
| Stars | 55 | cumulative | GitHub REST `repos/{repo}` |
| Forks | 16 | cumulative | GitHub REST `repos/{repo}` |
| Watchers | 2 | cumulative | GitHub REST `repos/{repo}` |
| Clones | 76 unique | 14 days, rolling | GitHub REST `traffic/clones` |
| Repository views | 115 unique | 14 days, rolling | GitHub REST `traffic/views` |
| Release asset downloads | **unmeasurable** | cumulative | GitHub REST `releases` |
| GHCR image pulls | **unmeasurable** | cumulative | package page for `ghcr.io/armpro24-blip/aieng-workbench` |
| `.aieng` packages created | **unmeasurable** | cumulative | per-installation only |
| `pip`/`uvx` installs from a release tag | **unmeasurable** | cumulative | no counter exists |
| Third-party MCP integrations | **unmeasurable** | cumulative | manual — known client configs pointing at this server |

Total in the same 14-day window: 358 clones, 435 views. The unique figures are
the ones worth tracking; the totals are inflated by repeated CI and tooling
fetches.

## What the numbers mean, and do not

**Unmeasurable is not zero.** Five of the ten signals have no counter at all.
Reporting them as `0` would turn "we cannot see" into "nobody uses it", which is
the same defect the release docs already carried when they promised a PyPI
publication that was never coming. The capture script emits
`unmeasurable` plus a reason and never a fabricated number.

**Interest is not use.** Stars, forks and watchers measure attention. None of
them implies a single run of the workbench.

**Every visible count is an upper bound on human use.** Clones include CI,
mirrors, and tooling. GHCR pulls, if we ever read them, include this repo's own
Docker smoke job pulling the image it just pushed.

**Two signals roll off.** GitHub traffic covers 14 days only. A month with no
capture is a permanent hole — this is the reason to run the script on a
schedule rather than when someone remembers.

### Why each unmeasurable signal is unmeasurable

| Signal | Why | Fixable? |
|---|---|---|
| Release asset downloads | At capture time neither release had an attached asset, and GitHub exposes no download count for the auto-generated source archives | **Yes, and fixed** — `release.yml` now attaches the built wheels/sdists, so releases cut from here on carry a real counter |
| GHCR image pulls | The packages REST API needs a `read:packages` token, and even then exposes no pull counter for container packages; the package web page is the only source | Partly — a manual read, recorded by hand |
| `.aieng` packages created | The workbench has no telemetry and will not get any; packages live in the user's own data directory | No, by design |
| `pip`/`uvx` installs from a tag | A git-based install is a clone, indistinguishable from any other clone | No — this is the standing cost of the no-PyPI decision (#273) |
| Third-party MCP integrations | Nothing observes a third party's client config | No — countable only when someone tells us |

## Non-goals

No analytics in the product. No phone-home from the MCP server, the backend, or
the workbench UI. Everything above is read from the repository's own hosting
side, and a signal that would require instrumenting a user's installation is
listed as unmeasurable rather than acquired.
