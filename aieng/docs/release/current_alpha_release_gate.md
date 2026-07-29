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

1. **One-time:** configure PyPI/TestPyPI Trusted Publishing and the `pypi` /
   `testpypi` GitHub Environments. Until this is done nothing can publish at
   all. See the runbook's *Automated path*.
2. Publish `aieng-format` and `aieng-workbench-mcp` as explicit alpha/pre-release
   artifacts — `gh workflow run release.yml -f target=testpypi` for the dry run,
   then push the `v*` tag for PyPI.
3. Update install snippets only after the real published artifact names and
   versions are confirmed.
4. Record baseline embedding-depth metrics.
5. Confirm the GitHub release the workflow created reads correctly for the tag.

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
| PyPI `aieng-format` | not published |
| PyPI `aieng-workbench-mcp` | not published |
| TestPyPI (both) | not published |
| Git tag / GitHub release | none exist in this repository |

The Docker path is therefore already externally usable; PyPI is the only
unpublished distribution channel.

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

## Post-Publish Install Verification

Use a clean environment and published artifact path. Replace versions with the
actual published alpha versions.

```bash
python -m venv .tmp-alpha-install
.tmp-alpha-install\Scripts\python -m pip install --upgrade pip
.tmp-alpha-install\Scripts\python -m pip install --pre aieng-format==<published-alpha>
.tmp-alpha-install\Scripts\python -m pip install --pre aieng-workbench-mcp==<published-alpha>
.tmp-alpha-install\Scripts\python -c "import aieng; print(aieng.FORMAT_VERSION)"
.tmp-alpha-install\Scripts\python -c "from importlib.resources import files; print(files('aieng.schemas').joinpath('manifest.schema.json').is_file())"
```

For Docker:

```bash
docker pull ghcr.io/armpro24-blip/aieng-workbench:<release-tag-or-sha-tag>
```

## Embedding-Depth Baseline

Record the first baseline immediately after publication. Unknown values are
acceptable at tag time, but they should be explicit rather than silently omitted.

| Signal | Baseline value | Source | Date |
|---|---:|---|---|
| `aieng-format` published installs/downloads | unknown | PyPI/TestPyPI stats or owner note | TBD |
| `aieng-workbench-mcp` published installs/downloads | unknown | PyPI/TestPyPI stats or owner note | TBD |
| GHCR pulls | unknown | GHCR package page or owner note | TBD |
| External-agent packaged-path runs | 1+ expected | dogfood evidence packet | TBD |
| `.aieng` packages created from published path | unknown | dogfood/project evidence | TBD |

## Honesty Boundary

This gate does not certify engineering correctness, solver validity, or CAD
modeling quality. It only records whether the alpha artifacts are installable,
auditable, and externally dogfoodable.
