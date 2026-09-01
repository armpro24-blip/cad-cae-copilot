# Current Alpha Release Gate

Status: **owner-action gate, not an automatic release**.

This note reconciles the current repository state with the open release trackers
[#152](https://github.com/armpro24-blip/cad-cae-copilot/issues/152) and
[#273](https://github.com/armpro24-blip/cad-cae-copilot/issues/273). It is a
pre-tag checklist for the current `main` line, not a historical release-branch
record.

## Already Evidenced On Main

- CI, packaging smoke, and Docker smoke are green on recent release-gate work.
- The GHCR Docker path exists for the all-in-one workbench image:
  `ghcr.io/armpro24-blip/aieng-workbench:latest` plus immutable `sha-*` tags.
- Packaged Docker/MCP dogfood evidence exists under
  `docs/dogfood/issue-179-packaged-external-agent.md`.
- The review handoff path is present:
  `GET /api/projects/{project_id}/review-support-packet/preview` and
  `POST /api/projects/{project_id}/review-support-packet/export`.
- The Web Workbench exposes the review packet export entry point.

## Still Owner-Gated

These actions must be performed by a human owner with the relevant package and
release credentials. Do not infer completion from green CI alone.

1. Record baseline embedding-depth metrics.
2. Confirm the GitHub release the workflow created reads correctly for the tag.

**PyPI is out of scope, by owner decision (2026-09-01).** Distribution is the
Git tag plus the GHCR image; `aieng-format` and `aieng-workbench-mcp` are not
published to PyPI or TestPyPI and are not planned to be. Neither name is
registered there, so an install snippet naming a public index would point at
someone else's upload — the READMEs and `MCP_SETUP.md` therefore document the
`pip`/`uvx`-from-tag form as the install path, not as a stopgap. The publish
jobs in [`release.yml`](../../../.github/workflows/release.yml) remain, unused
and dispatch-only: they need Trusted Publishing configured before they could
run at all, which is the one-time owner action nobody is taking. Reversing the
decision means doing that setup, then updating the snippets and the guard list
in `aieng-ui/backend/tests/test_release_semantic_surfaces.py`.

Steps that used to be listed here and are now mechanised by
[`.github/workflows/release.yml`](../../../.github/workflows/release.yml):
building both dists, `twine check`, publishing in dependency order,
**verifying a clean install from the published artifacts outside the source
tree**, and creating the prerelease. Mechanised does not mean unattended — the
workflow only starts from a tag push or manual dispatch, and the publish jobs
sit behind environments that can require an approval click.

## Current Channel Status

| Channel | State |
|---|---|
| GHCR `ghcr.io/armpro24-blip/aieng-workbench` | **published** — `latest` plus immutable `sha-*` tags, pushed by `docker-smoke.yml` on every green `main` |
| PyPI `aieng-format` | **not published, and not planned** — name unregistered |
| PyPI `aieng-workbench-mcp` | **not published, and not planned** — name unregistered |
| TestPyPI (both) | not published, and not planned |
| Git tag / GitHub release | **published** — `v0.1.0-alpha.3` and `v0.1.0-alpha.4` prereleases |

Both shipped channels — the Git tag and the GHCR image — are externally usable,
so there is no unpublished channel blocking use. PyPI is a channel this project
has chosen not to use, not an outstanding gap.

## Minimum Pre-Tag Verification

Run from the repository root unless noted.

```bash
python scripts/update_version_surface.py --check
python -m pytest aieng-ui/backend/tests/test_review_support_packet.py -q
python -m pytest aieng-ui/backend/tests/test_aieng_package_handoff_runbook.py -q
python -m pytest aieng-ui/backend/tests/test_value_demo_packet.py -q
```

Then confirm the remote checks for the tag candidate commit:

```bash
gh run list --branch main --limit 10
```

Required remote workflows for the release commit:

- CI
- Packaging smoke
- Docker smoke, when the release includes the Docker published-image path

## Post-Release Install Verification

Use a clean environment and the **published channel** — the release tag. There
is no index install to verify (see the channel table above); replace the tag
with the one just cut.

```bash
python -m venv .tmp-alpha-install
.tmp-alpha-install\Scripts\python -m pip install --upgrade pip
.tmp-alpha-install\Scripts\python -m pip install "aieng-format @ git+https://github.com/armpro24-blip/cad-cae-copilot.git@v0.1.0-alpha.4#subdirectory=aieng"
.tmp-alpha-install\Scripts\python -m pip install "aieng-workbench-mcp @ git+https://github.com/armpro24-blip/cad-cae-copilot.git@v0.1.0-alpha.4#subdirectory=aieng-ui/backend"
.tmp-alpha-install\Scripts\python -c "import aieng; print(aieng.FORMAT_VERSION)"
.tmp-alpha-install\Scripts\python -c "from importlib.resources import files; print(files('aieng.schemas').joinpath('manifest.schema.json').is_file())"
```

For Docker:

```bash
docker pull ghcr.io/armpro24-blip/aieng-workbench:<release-tag-or-sha-tag>
```

## Embedding-Depth Baseline

Captured, with the gaps named, in
[`embedding_depth_baseline.md`](embedding_depth_baseline.md) — first baseline
2026-09-01, recapture with
`python scripts/capture_embedding_depth_baseline.py`.

The table that used to sit here read "installs" as PyPI download counts, so
every row was `unknown / TBD` and would have stayed that way forever: PyPI is
not a channel this project uses. Five of the ten signals genuinely have no
counter, and the baseline doc says which and why rather than reporting a zero.
The one that was cheap to fix is fixed — `release.yml` now attaches the built
wheels to the release, which is both an index-free install path and the only
per-artifact download counter these channels can offer.

## Honesty Boundary

This gate does not certify engineering correctness, solver validity, or CAD
modeling quality. It only records whether the alpha artifacts are installable,
auditable, and externally dogfoodable.
