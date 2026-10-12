"""
analyze.py – Visualization and report generation for the v2 benchmark.

Reads the CSVs produced by benchmark.py and generates:
  figures/decision_accuracy.png     Bar chart: accuracy per system.
  figures/unsafe_allows.png         Bar chart: unsafe ALLOW counts.
  figures/cache_hit_rate.png        Bar chart: cache hit rates.
  figures/latency_ci.png            Mean latency with 95% confidence intervals
                                    across repetitions.
  figures/family_heatmap.png        Per-family accuracy heatmap.
  figures/work_savings.png          Full evaluations avoided vs full_recompute.
  figures/commit_gate.png           Commit gate correctness per system.
  figures/safety_efficiency.png     Safety vs efficiency frontier scatter.
  results/REPORT.md                 Human-readable benchmark report.

Usage:
    python src/analyze.py
    python src/analyze.py --input results/run_metrics.csv --out figures
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from runtime import SAFE_SYSTEMS, UNSAFE_SYSTEMS, RUNTIME_REGISTRY
from scenarios import FAMILIES


# ---------------------------------------------------------------------------
# Labels and colors
# ---------------------------------------------------------------------------

LABELS = {
    "full_recompute":       "Full recompute",
    "tapr_dep_cache":       "TAPR dep-cache ★",
    "exact_snapshot_cache": "Exact snapshot",
    "coarse_cache":         "Coarse cache ⚠",
    "stale_proof_accept":   "Stale proof ⚠",
    "no_commit_gate":       "No commit gate ⚠",
    "pathless_policy":      "Pathless policy ⚠",
}

COLORS = {
    "full_recompute":       "#2196F3",
    "tapr_dep_cache":       "#4CAF50",
    "exact_snapshot_cache": "#9C27B0",
    "coarse_cache":         "#F44336",
    "stale_proof_accept":   "#FF9800",
    "no_commit_gate":       "#E91E63",
    "pathless_policy":      "#795548",
}


def _color(name: str) -> str:
    return COLORS.get(name, "#607D8B")


def _label(name: str) -> str:
    return LABELS.get(name, name)


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _num(v: Any) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return math.nan


def _systems_in(rows: List[Dict]) -> List[str]:
    """Return system names in a stable display order."""
    seen = dict.fromkeys(r["system"] for r in rows)
    ordered = []
    for s in RUNTIME_REGISTRY:
        if s in seen:
            ordered.append(s)
    for s in seen:
        if s not in ordered:
            ordered.append(s)
    return ordered


def _vals(rows: List[Dict], system: str, field: str) -> List[float]:
    return [_num(r[field]) for r in rows
            if r["system"] == system and not math.isnan(_num(r.get(field, "nan")))]


def _mean_ci(xs: List[float], z: float = 1.96) -> tuple[float, float]:
    """Return (mean, half_width) for a 95% CI using t-distribution approx."""
    if len(xs) < 2:
        return (xs[0] if xs else 0.0, 0.0)
    m = statistics.mean(xs)
    s = statistics.stdev(xs)
    hw = z * s / math.sqrt(len(xs))
    return m, hw


# ---------------------------------------------------------------------------
# Individual chart functions
# ---------------------------------------------------------------------------

def _bar_chart(systems, values, title, ylabel, out_path,
               err=None, annotate=True, safe_mark=True):
    from svg_chart import render_bar_chart
    render_bar_chart(systems, values, title, ylabel, Path(out_path), err=err)
    if not HAS_MPL:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    xs = range(len(systems))
    colors = [_color(s) for s in systems]
    bars = ax.bar(xs, values, color=colors,
                  yerr=err if err else None,
                  capsize=4, edgecolor="white", linewidth=0.8)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([_label(s) for s in systems],
                       rotation=25, ha="right", fontsize=9)
    ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left", fontweight="bold", pad=12)
    ax.grid(axis="y", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if annotate:
        for bar, v in zip(bars, values):
            if not math.isnan(v):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + (max(values) * 0.01 if max(values) else 0.001),
                        f"{v:.2f}", ha="center", va="bottom", fontsize=8)
    if safe_mark:
        legend = [
            mpatches.Patch(color="#4CAF50", label="Safe system"),
            mpatches.Patch(color="#F44336", label="Unsafe / negative control"),
        ]
        ax.legend(handles=legend, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    plt.close(fig)


def chart_decision_accuracy(rows, systems, out):
    vals = []
    errs = []
    for s in systems:
        xs = _vals(rows, s, "accuracy")
        m, hw = _mean_ci(xs) if xs else (0.0, 0.0)
        vals.append(m * 100)
        errs.append(hw * 100)
    _bar_chart(systems, vals, "Decision accuracy (oracle agreement)",
               "Accuracy (%)", out / "decision_accuracy.png", err=errs)


def chart_unsafe_allows(rows, systems, out):
    vals = [statistics.mean(_vals(rows, s, "unsafe_allows") or [0])
            for s in systems]
    _bar_chart(systems, vals, "Unsafe ALLOW decisions (lower is better; 0 = safe)",
               "Mean unsafe ALLOWs per rep", out / "unsafe_allows.png")


def chart_cache_hit_rate(rows, systems, out):
    vals = [statistics.mean(_vals(rows, s, "cache_hit_rate") or [0]) * 100
            for s in systems]
    _bar_chart(systems, vals, "Cache / proof hit rate",
               "Hit rate (%)", out / "cache_hit_rate.png")


def chart_latency_ci(rows, systems, out):
    if not HAS_MPL:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    xs = list(range(len(systems)))
    for i, s in enumerate(systems):
        lats = [v / 1_000 for v in _vals(rows, s, "mean_latency_ns")]  # → μs
        if not lats:
            continue
        m, hw = _mean_ci(lats)
        ax.errorbar(i, m, yerr=hw, fmt="o", color=_color(s),
                    capsize=6, markersize=8, linewidth=2)
    ax.set_xticks(xs)
    ax.set_xticklabels([_label(s) for s in systems],
                       rotation=25, ha="right", fontsize=9)
    ax.set_ylabel("Mean decision latency (μs)")
    ax.set_title("Latency with 95% CI across repeated runs",
                 loc="left", fontweight="bold", pad=12)
    ax.grid(alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "latency_ci.png", bbox_inches="tight", dpi=150)
    plt.close(fig)


def chart_work_savings(rows, systems, out):
    """Ratio of full evaluations vs full_recompute (lower = more savings)."""
    full_evals = {}
    for s in systems:
        xs = _vals(rows, s, "full_evaluations")
        full_evals[s] = statistics.mean(xs) if xs else 0.0
    baseline = full_evals.get("full_recompute", 1.0) or 1.0
    vals = [(full_evals.get(s, 0) / baseline) * 100 for s in systems]
    _bar_chart(systems, vals,
               "Full evaluations (% of full_recompute baseline, lower = more reuse)",
               "% of baseline evaluations", out / "work_savings.png")


def chart_commit_gate(rows, systems, out):
    """Stale-proof accepts at commit gate (should be 0 for safe systems)."""
    vals = [statistics.mean(_vals(rows, s, "stale_commit_accepts") or [0])
            for s in systems]
    _bar_chart(systems, vals,
               "Stale proofs accepted at commit gate (should be 0 for safe systems)",
               "Mean stale commit accepts per rep",
               out / "commit_gate.png")


def chart_family_heatmap(family_rows, systems, out):
    if not HAS_MPL:
        return
    families = FAMILIES
    safe_sys = [s for s in systems if s in SAFE_SYSTEMS]
    if not safe_sys:
        safe_sys = systems[:4]

    acc: Dict[str, Dict[str, float]] = {s: {} for s in safe_sys}
    for row in family_rows:
        s = row.get("system", "")
        f = row.get("family", "")
        if s in acc:
            acc[s][f] = _num(row.get("accuracy", "nan"))

    matrix = [[acc[s].get(f, math.nan) * 100
                for s in safe_sys] for f in families]

    fig, ax = plt.subplots(figsize=(max(6, len(safe_sys) * 2), len(families) * 0.7 + 1))
    import numpy as np
    data = np.array([[v if not math.isnan(v) else 50.0 for v in row]
                     for row in matrix])
    im = ax.imshow(data, vmin=50, vmax=100, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(safe_sys)))
    ax.set_xticklabels([_label(s) for s in safe_sys], rotation=25,
                       ha="right", fontsize=8)
    ax.set_yticks(range(len(families)))
    ax.set_yticklabels(families, fontsize=8)
    ax.set_title("Per-family accuracy (safe systems only)",
                 loc="left", fontweight="bold", pad=12)
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("Oracle agreement (%)")
    for i, row in enumerate(matrix):
        for j, v in enumerate(row):
            if not math.isnan(v):
                ax.text(j, i, f"{v:.0f}", ha="center", va="center",
                        fontsize=8,
                        color="white" if v < 70 else "black")
    fig.tight_layout()
    fig.savefig(out / "family_heatmap.png", bbox_inches="tight", dpi=150)
    plt.close(fig)


def chart_safety_efficiency(rows, systems, out):
    """Scatter: cache hit rate (efficiency) vs accuracy (safety)."""
    hit_rates = [statistics.mean(_vals(rows, s, "cache_hit_rate") or [0]) * 100 for s in systems]
    accuracies = [statistics.mean(_vals(rows, s, "accuracy") or [0]) * 100 for s in systems]
    from svg_chart import render_scatter_frontier
    render_scatter_frontier(systems, hit_rates, accuracies, out / "safety_efficiency.png")
    if not HAS_MPL:
        return
    fig, ax = plt.subplots(figsize=(8, 6))
    for s in systems:
        hit  = statistics.mean(_vals(rows, s, "cache_hit_rate") or [0])
        acc  = statistics.mean(_vals(rows, s, "accuracy") or [0])
        ax.scatter(hit * 100, acc * 100, color=_color(s),
                   s=120, zorder=5, label=_label(s))
        ax.annotate(_label(s), (hit * 100, acc * 100),
                    textcoords="offset points", xytext=(6, 4), fontsize=7)
    ax.set_xlabel("Cache hit rate (%)")
    ax.set_ylabel("Decision accuracy (%)")
    ax.set_title("Safety–efficiency frontier\n"
                 "(top-right = ideal: high reuse AND correct decisions)",
                 loc="left", fontweight="bold", pad=12)
    ax.set_xlim(-5, 105)
    ax.set_ylim(40, 105)
    ax.axhline(100, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
    ax.grid(alpha=0.2)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "safety_efficiency.png", bbox_inches="tight", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(rows, systems, family_rows, out_path: Path) -> None:
    lines = [
        "# Agent Runtime Research Lab v2 – Benchmark Report",
        "",
        "> **Scope**: synthetic, deterministic, offline benchmark.  "
        "Results characterise this implementation on this workload only.  "
        "They do not constitute evidence of real-world agent safety, "
        "novelty relative to prior work, or superiority over production "
        "policy engines.",
        "",
        "## Why stateful scenarios?",
        "",
        "Current AI agent benchmarks almost exclusively measure *language quality* "
        "(final answer text, tool-call accuracy).  This benchmark instead measures "
        "the **runtime governance layer**: the system that decides whether a typed "
        "proposed action is admissible given the current world state, execution path, "
        "evidence, and active policy.  Scenarios are stateful event sequences, not "
        "isolated prompts.  This makes it possible to measure the most dangerous "
        "failure mode: a runtime that serves a cached ALLOW decision after the "
        "authorization has been revoked.",
        "",
        "## Systems",
        "",
        "| System | Type | Description |",
        "|--------|------|-------------|",
        "| Full recompute | Safe baseline | Always evaluates from scratch. |",
        "| TAPR dep-cache | Safe (hypothesis) | Cache keyed by explicit dependency set; evicts on any dependency change; validates at commit gate. |",
        "| Exact snapshot | Safe comparison | Cache keyed by full world-state hash. Sound but low hit rate when irrelevant metadata changes. |",
        "| Coarse cache | **Unsafe** control | Cache keyed by action+target only. Ignores authorization and evidence. |",
        "| Stale proof accept | **Unsafe** control | Cache without dependency tracking; never evicts. |",
        "| No commit gate | **Unsafe** control | TAPR cache but skips TOCTOU check at commit boundary. |",
        "| Pathless policy | **Unsafe** control | Full recompute but path history is zeroed; cannot enforce path-dependent rules. |",
        "",
        "## Results",
        "",
        "| System | Accuracy | Unsafe ✗ | Cache hit | Full evals | Lat μs |",
        "|--------|----------|----------|-----------|------------|--------|",
    ]

    for s in systems:
        acc  = statistics.mean(_vals(rows, s, "accuracy")        or [0])
        uns  = statistics.mean(_vals(rows, s, "unsafe_allows")   or [0])
        hit  = statistics.mean(_vals(rows, s, "cache_hit_rate")  or [0])
        work = statistics.mean(_vals(rows, s, "full_evaluations") or [0])
        lat  = statistics.mean(_vals(rows, s, "mean_latency_ns") or [0]) / 1_000
        tag  = " ⚠" if s in UNSAFE_SYSTEMS else ""
        lines.append(
            f"| {_label(s)}{tag} | {acc:.1%} | {uns:.1f} | "
            f"{hit:.1%} | {work:.0f} | {lat:.2f} |"
        )

    lines += [
        "",
        "## Figures",
        "",
        "![Decision accuracy](figures/decision_accuracy.png)",
        "",
        "![Unsafe allows](figures/unsafe_allows.png)",
        "",
        "![Cache hit rate](figures/cache_hit_rate.png)",
        "",
        "![Latency CI](figures/latency_ci.png)",
        "",
        "![Work savings](figures/work_savings.png)",
        "",
        "![Commit gate](figures/commit_gate.png)",
        "",
        "![Family heatmap](figures/family_heatmap.png)",
        "",
        "![Safety–efficiency frontier](figures/safety_efficiency.png)",
        "",
        "## Interpretation limits",
        "",
        "- The oracle and evaluator share a human-authored policy contract. "
          "They are separately implemented, but not independently authored.",
        "- Scenarios are synthetic and typed. No LLM, no natural-language parsing.",
        "- Proof-construction cost is a deterministic CPU-work proxy, "
          "not a real theorem prover.",
        "- The commit gate is in-process and deterministic. "
          "Real concurrent TOCTOU requires atomic compare-and-swap.",
        "- Latency is wall-clock Python `perf_counter_ns`. "
          "Do not compare across machines.",
        "- No OPA/Rego or Cedar adapter is included in this run. "
          "See `EXTERNAL_BASELINES.md`.",
        "",
        "## What to do next before a credible research claim",
        "",
        "1. Author policy cases *independently* of the evaluator; "
           "freeze the contract before running comparisons.",
        "2. Add real OPA/Rego and Cedar adapters with shared semantic-equivalence tests.",
        "3. Implement a concurrent check/commit harness with "
           "deterministic race schedules.",
        "4. Sweep path length, dependency fan-out, evidence cardinality, "
           "and policy complexity.",
        "5. Measure the cost of dependency *discovery* vs the cost of recomputation.",
        "6. Add the language layer: intent extraction accuracy, "
           "constraint retention, ambiguity escalation.",
        "",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input",   default="results/run_metrics.csv")
    ap.add_argument("--family",  default="results/family_accuracy.csv")
    ap.add_argument("--out",     default="figures")
    ap.add_argument("--report",  default="results/REPORT.md")
    a = ap.parse_args()

    metrics_path = Path(a.input)
    family_path  = Path(a.family)
    out          = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if not metrics_path.exists():
        print(f"ERROR: {metrics_path} not found. Run benchmark.py first.")
        sys.exit(1)

    rows   = _load_csv(metrics_path)
    f_rows = _load_csv(family_path) if family_path.exists() else []
    systems = _systems_in(rows)

    print(f"Loaded {len(rows)} metric rows, {len(systems)} systems.")
    print("Generating charts …")

    chart_decision_accuracy(rows, systems, out)
    chart_unsafe_allows(rows, systems, out)
    chart_cache_hit_rate(rows, systems, out)
    chart_latency_ci(rows, systems, out)
    chart_work_savings(rows, systems, out)
    chart_commit_gate(rows, systems, out)
    if f_rows:
        try:
            import numpy  # noqa: F401
            chart_family_heatmap(f_rows, systems, out)
        except ImportError:
            print("  (numpy not available; skipping family heatmap)")
    chart_safety_efficiency(rows, systems, out)

    print("Generating report …")
    generate_report(rows, systems, f_rows, Path(a.report))

    print(f"Charts → {out.resolve()}/")
    print(f"Report → {Path(a.report).resolve()}")


if __name__ == "__main__":
    main()
