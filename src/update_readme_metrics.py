"""Refresh the README's measured-results block from one completed run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from statistics import mean


START = "<!-- TAPR_RESULTS:BEGIN -->"
END = "<!-- TAPR_RESULTS:END -->"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True,
        check=False,
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    run_dir = args.run_dir.resolve()
    relative_run = run_dir.relative_to(root).as_posix()
    benchmark = read_csv(run_dir / "run_metrics.csv")
    fuzz = read_csv(run_dir / "probe_c_metrics.csv")
    if not benchmark or not fuzz:
        raise SystemExit("Run metrics and Probe C metrics must be non-empty.")

    profiles = []
    for profile_name in ("high_reuse", "low_reuse"):
        selected = {
            system: [row for row in fuzz
                     if row["profile"] == profile_name
                     and row["system"] == system]
            for system in ("full_recompute", "tapr_dep_cache")
        }
        if not all(selected.values()):
            raise SystemExit(f"Missing full/TAPR rows for {profile_name}.")
        counts = {
            system: sum(int(row["decisions"]) for row in selected[system])
            for system in selected
        }
        hits = {
            system: sum(int(row["cache_hits"]) for row in selected[system])
            for system in selected
        }
        hit_rates = {
            system: hits[system] / counts[system] if counts[system] else 0.0
            for system in selected
        }
        profiles.append({
            "profile": profile_name,
            "decisions": counts["tapr_dep_cache"],
            "full_cache_hit_rate": hit_rates["full_recompute"],
            "tapr_cache_hit_rate": hit_rates["tapr_dep_cache"],
            "hit_rate_difference": (
                hit_rates["tapr_dep_cache"] - hit_rates["full_recompute"]
            ),
        })

    def aggregate(system: str, metric: str) -> float:
        selected = [row for row in benchmark if row["system"] == system]
        if not selected:
            raise SystemExit(f"Missing benchmark rows for {system}.")
        return mean(float(row[metric]) for row in selected)

    data = {
        "run_dir": relative_run,
        "seed": json.loads((run_dir / "manifest.json").read_text())["seed"],
        "source_revision": git(root, "rev-parse", "HEAD"),
        "profiles": profiles,
        "benchmark": {
            "tapr_proposal_accuracy": aggregate("tapr_dep_cache", "accuracy"),
            "full_proposal_accuracy": aggregate("full_recompute", "accuracy"),
            "tapr_commit_accuracy": aggregate(
                "tapr_dep_cache", "commit_accuracy"
            ),
            "full_commit_accuracy": aggregate(
                "full_recompute", "commit_accuracy"
            ),
            "tapr_commit_false_denies": aggregate(
                "tapr_dep_cache", "commit_false_denies"
            ),
            "full_commit_false_denies": aggregate(
                "full_recompute", "commit_false_denies"
            ),
        },
        "source_csv_sha256": {
            "run_metrics.csv": sha256(run_dir / "run_metrics.csv"),
            "probe_c_metrics.csv": sha256(run_dir / "probe_c_metrics.csv"),
        },
    }
    profile_table = [
        "| Workload profile | Decisions | Full-recompute cache hit | TAPR cache hit | Difference (percentage points) |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in profiles:
        profile_table.append(
            f"| {row['profile']} | {row['decisions']} | "
            f"{row['full_cache_hit_rate']:.2%} | "
            f"{row['tapr_cache_hit_rate']:.2%} | "
            f"{100 * row['hit_rate_difference']:.2f} |"
        )
    block = "\n".join([
        START,
        "## Data-derived benchmark snapshot",
        "",
        f"Generated from `{relative_run}` (seed `{data['seed']}`; "
        f"source revision `{data['source_revision']}`). These are descriptive "
        "synthetic-run measurements, not production guarantees.",
        "",
        f"- Proposal accuracy: full recompute "
        f"{data['benchmark']['full_proposal_accuracy']:.2%}; TAPR "
        f"{data['benchmark']['tapr_proposal_accuracy']:.2%}.",
        f"- Commit-boundary accuracy: full recompute "
        f"{data['benchmark']['full_commit_accuracy']:.2%}; TAPR "
        f"{data['benchmark']['tapr_commit_accuracy']:.2%}.",
        f"- Mean commit false-denials per repetition: full recompute "
        f"{data['benchmark']['full_commit_false_denies']:.2f}; TAPR "
        f"{data['benchmark']['tapr_commit_false_denies']:.2f}.",
        "- Cache-hit rates by reuse regime (weighted by decision count):",
        "",
        *profile_table,
        "",
        f"Raw metrics: [{relative_run}]({relative_run}/REPORT.md); "
        f"integrity data: [{relative_run}/README_metrics.json]"
        f"({relative_run}/README_metrics.json).",
        END,
    ])
    readme = root / "README.md"
    content = readme.read_text()
    if START in content and END in content:
        before, rest = content.split(START, 1)
        _, after = rest.split(END, 1)
        content = before.rstrip() + "\n\n" + block + after
    else:
        content = content.rstrip() + "\n\n" + block + "\n"
    readme.write_text(content)
    (run_dir / "README_metrics.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n"
    )

    source_paths = [
        *sorted((root / "src").rglob("*.py")),
        *sorted((root / "tests").rglob("*.py")),
        root / "README.md",
        root / "RESEARCH_CHARTER.md",
        root / "run_lab.sh",
        root / "reproduce_research.sh",
        root / "requirements.txt",
    ]
    source_checksums = {
        str(path.relative_to(root)): sha256(path)
        for path in source_paths if path.is_file()
    }
    revision = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--short")
    for manifest_name in (
        "manifest.json", "sweep_manifest.json", "falsification_manifest.json"
    ):
        manifest_path = run_dir / manifest_name
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        manifest["source_revision"] = revision
        manifest["worktree_dirty"] = bool(status)
        manifest["worktree_status"] = status
        manifest["source_sha256"] = source_checksums
        if manifest_name == "manifest.json":
            manifest["machine"] = platform.machine()
            manifest["processor"] = platform.processor()
        if manifest_name == "manifest.json":
            manifest["output_sha256"] = {
                path.name: sha256(path)
                for path in sorted(run_dir.iterdir())
                if path.is_file() and path.name != "manifest.json"
                and path.name not in {
                    "falsification_manifest.json", "sweep_manifest.json"
                }
            }
        elif manifest_name == "sweep_manifest.json":
            manifest["output_sha256"] = {
                "sweep.csv": sha256(run_dir / "sweep.csv")
            } if (run_dir / "sweep.csv").exists() else {}
        else:
            manifest["generated_artifact_sha256"] = {
                str(path.relative_to(run_dir)): sha256(path)
                for path in sorted(run_dir.rglob("*"))
                if path.is_file()
                and path.name != "falsification_manifest.json"
            }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"Updated README from {relative_run}")


if __name__ == "__main__":
    main()
