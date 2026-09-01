"""Capture the embedding-depth baseline from the channels that actually exist.

Issue #510. `aieng/docs/strategic_direction_2026.md` §3a names MCP-server
installs, `.aieng` packages created, and third-party integrations as the real
success signal, and the alpha release gate read "installs" as PyPI download
counts. PyPI is out of scope by owner decision (#273), so that number will never
exist — this script measures what can be measured and says so plainly about the
rest.

Two rules it follows, because the alternative is a metrics table that lies:

* An unmeasurable signal is reported as ``unmeasurable`` with the reason. It is
  never reported as ``0`` — a zero is a measurement, and reading "no telemetry"
  as "no users" would be the same defect the release docs already had.
* Every number carries its capture date and its source, because two of these
  signals are rolling windows that are simply gone if nobody looked.

Usage::

    python scripts/capture_embedding_depth_baseline.py            # human table
    python scripts/capture_embedding_depth_baseline.py --json     # machine
    python scripts/capture_embedding_depth_baseline.py --markdown # doc table

Needs the `gh` CLI authenticated with repo access. Read-only: it calls the
GitHub REST API and touches nothing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any

REPO = "armpro24-blip/cad-cae-copilot"
CONTAINER_PACKAGE = "ghcr.io/armpro24-blip/aieng-workbench"


@dataclass
class Signal:
    """One measured (or explicitly unmeasurable) number."""

    key: str
    label: str
    source: str
    value: int | None = None
    unit: str = ""
    window: str = "cumulative"
    unmeasurable_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def measurable(self) -> bool:
        return self.unmeasurable_reason is None

    def rendered_value(self) -> str:
        if not self.measurable:
            return "unmeasurable"
        if self.value is None:
            return "unknown"
        return f"{self.value:,}{(' ' + self.unit) if self.unit else ''}"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "source": self.source,
            "window": self.window,
        }
        if self.measurable:
            payload["value"] = self.value
            if self.unit:
                payload["unit"] = self.unit
        else:
            payload["value"] = None
            payload["unmeasurable_reason"] = self.unmeasurable_reason
        if self.notes:
            payload["notes"] = self.notes
        return payload


def _gh_api(path: str) -> Any | None:
    """Return parsed JSON, or None when the call is refused/unavailable."""
    try:
        result = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _repo_interest() -> list[Signal]:
    data = _gh_api(f"repos/{REPO}")
    if not isinstance(data, dict):
        return [
            Signal(
                "repo_interest",
                "Repository interest (stars / forks / watchers)",
                "GitHub REST `repos/{repo}`",
                unmeasurable_reason="the API call failed — check `gh auth status`",
            )
        ]
    return [
        Signal("stars", "Stars", "GitHub REST `repos/{repo}`", data.get("stargazers_count")),
        Signal("forks", "Forks", "GitHub REST `repos/{repo}`", data.get("forks_count")),
        Signal(
            "watchers",
            "Watchers",
            "GitHub REST `repos/{repo}`",
            data.get("subscribers_count"),
            notes=["interest, not use — a star is not an install"],
        ),
    ]


def _traffic() -> list[Signal]:
    signals: list[Signal] = []
    for kind, label in (("clones", "Clones"), ("views", "Repository views")):
        data = _gh_api(f"repos/{REPO}/traffic/{kind}")
        if not isinstance(data, dict):
            signals.append(
                Signal(
                    kind,
                    label,
                    f"GitHub REST `traffic/{kind}`",
                    unmeasurable_reason=(
                        "needs push access on the repository; the API refused the call"
                    ),
                    window="14 days, rolling",
                )
            )
            continue
        signals.append(
            Signal(
                kind,
                label,
                f"GitHub REST `traffic/{kind}`",
                data.get("uniques"),
                unit="unique",
                window="14 days, rolling",
                notes=[
                    f"total in the same window: {data.get('count')}",
                    "rolls off after 14 days — a gap in capture is a permanent gap",
                    "CI and bots are counted; treat as an upper bound",
                ],
            )
        )
    return signals


def _release_assets() -> list[Signal]:
    """Downloads of files attached to a release — the only per-artifact counter
    the no-PyPI channel set can offer."""
    data = _gh_api(f"repos/{REPO}/releases")
    if not isinstance(data, list):
        return [
            Signal(
                "release_asset_downloads",
                "Release asset downloads",
                "GitHub REST `releases`",
                unmeasurable_reason="the API call failed — check `gh auth status`",
            )
        ]

    total = 0
    with_assets = 0
    for release in data:
        assets = release.get("assets") or []
        if assets:
            with_assets += 1
        total += sum(int(a.get("download_count") or 0) for a in assets)

    if with_assets == 0:
        return [
            Signal(
                "release_asset_downloads",
                "Release asset downloads",
                "GitHub REST `releases`",
                unmeasurable_reason=(
                    f"none of the {len(data)} releases has an attached asset, and "
                    "GitHub does not expose a download count for the auto-generated "
                    "source archives — so tag installs leave no counter at all"
                ),
                notes=[
                    "attaching the built wheels to the release makes this measurable",
                ],
            )
        ]
    return [
        Signal(
            "release_asset_downloads",
            "Release asset downloads",
            "GitHub REST `releases`",
            total,
            notes=[f"across {with_assets} release(s) carrying assets"],
        )
    ]


def _ghcr_pulls() -> list[Signal]:
    """GHCR is a published channel but not an instrumented one for us."""
    data = _gh_api("users/armpro24-blip/packages/container/aieng-workbench")
    reason = (
        "the packages API needs a `read:packages` token, and even with it the "
        "REST API exposes package versions but no pull counter for containers — "
        "the package web page is the only source, so this is a manual read"
    )
    if isinstance(data, dict) and "download_count" in data:
        return [
            Signal(
                "ghcr_pulls",
                "GHCR image pulls",
                f"GitHub packages API for `{CONTAINER_PACKAGE}`",
                int(data["download_count"]),
                notes=["CI pulls its own image; treat as an upper bound"],
            )
        ]
    return [
        Signal(
            "ghcr_pulls",
            "GHCR image pulls",
            f"package page for `{CONTAINER_PACKAGE}`",
            unmeasurable_reason=reason,
        )
    ]


def _local_only() -> list[Signal]:
    return [
        Signal(
            "aieng_packages_created",
            "`.aieng` packages created",
            "per-installation only",
            unmeasurable_reason=(
                "the workbench has no telemetry and will not get any — packages are "
                "created in the user's own data directory and never reported"
            ),
            notes=["countable only in a dogfood run we perform ourselves"],
        ),
        Signal(
            "tag_installs",
            "`pip`/`uvx` installs from a release tag",
            "no counter exists",
            unmeasurable_reason=(
                "a git-based install is a clone; it is indistinguishable from any "
                "other clone and has no per-package counter. This is the standing "
                "cost of the no-PyPI decision (#273), not an oversight"
            ),
        ),
        Signal(
            "third_party_integrations",
            "Third-party MCP integrations",
            "manual — known client configs pointing at this server",
            unmeasurable_reason="nothing observes a third party's MCP config",
        ),
    ]


def collect() -> dict[str, Any]:
    signals: list[Signal] = []
    for producer in (_repo_interest, _traffic, _release_assets, _ghcr_pulls, _local_only):
        signals.extend(producer())
    return {
        "captured_on": date.today().isoformat(),
        "repository": REPO,
        "channels": {
            "published": ["git tag / GitHub release", f"GHCR `{CONTAINER_PACKAGE}`"],
            "not_used": ["PyPI", "TestPyPI"],
        },
        "signals": {signal.key: signal.to_dict() for signal in signals},
        "honesty": (
            "An unmeasurable signal is not zero. Numbers from rolling windows are "
            "only valid for their capture date, and every count that includes CI "
            "traffic is an upper bound on human use."
        ),
    }


def _render_table(payload: dict[str, Any]) -> str:
    lines = [
        f"Embedding-depth baseline — captured {payload['captured_on']}",
        "",
        f"{'signal':<44} {'value':>16}  window",
        f"{'-' * 44} {'-' * 16}  {'-' * 18}",
    ]
    for key, entry in payload["signals"].items():
        value = entry.get("value")
        rendered = (
            "unmeasurable"
            if entry.get("unmeasurable_reason")
            else ("unknown" if value is None else f"{value:,}{' ' + entry['unit'] if entry.get('unit') else ''}")
        )
        lines.append(f"{entry['label'][:44]:<44} {rendered:>16}  {entry['window']}")
    lines.append("")
    for key, entry in payload["signals"].items():
        if entry.get("unmeasurable_reason"):
            lines.append(f"  {key}: {entry['unmeasurable_reason']}")
    return "\n".join(lines)


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"| Signal | Value | Window | Source |",
        "|---|---:|---|---|",
    ]
    for entry in payload["signals"].values():
        value = entry.get("value")
        if entry.get("unmeasurable_reason"):
            rendered = "**unmeasurable**"
        elif value is None:
            rendered = "unknown"
        else:
            rendered = f"{value:,}{' ' + entry['unit'] if entry.get('unit') else ''}"
        lines.append(
            f"| {entry['label']} | {rendered} | {entry['window']} | {entry['source']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the raw payload")
    parser.add_argument("--markdown", action="store_true", help="emit a doc table")
    args = parser.parse_args(argv)

    payload = collect()
    if args.json:
        print(json.dumps(payload, indent=2))
    elif args.markdown:
        print(_render_markdown(payload))
    else:
        print(_render_table(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
