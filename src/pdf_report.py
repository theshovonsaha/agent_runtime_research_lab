"""
pdf_report.py – Compile real vector research PDFs with embedded figures and tables.

Uses standalone vector SVG pages compiled to multi-page publication PDF
via rsvg-convert (backed by Cairo/Pango).
"""
from __future__ import annotations

import base64
import csv
import json
import subprocess
from pathlib import Path
from typing import Dict, List


PAGE_WIDTH = 595   # Standard A4 pts
PAGE_HEIGHT = 842  # Standard A4 pts


def _img_tag(png_path: Path, x: int, y: int, w: int, h: int) -> str:
    if not png_path.exists():
        return ""
    b64_str = base64.b64encode(png_path.read_bytes()).decode("ascii")
    return f'<image href="data:image/png;base64,{b64_str}" x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMid meet"/>'


def build_research_paper_pdf(
    root_dir: Path, out_pdf_path: Path, run_dir: Path = None,
    figure_dir: Path = None, temp_dir: Path = None,
) -> Path:
    """Build multi-page formal Research Paper PDF with real experimental data & graphs."""
    run_dir = Path(run_dir) if run_dir is not None else root_dir / "results"
    figure_dir = Path(figure_dir) if figure_dir is not None else root_dir / "figures"
    temp_dir = Path(temp_dir) if temp_dir is not None else out_pdf_path.parent / "pdf_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "run_metrics.csv"
    with metrics_path.open() as f:
        metrics = list(csv.DictReader(f))

    systems = []
    for r in metrics:
        if r["system"] not in systems:
            systems.append(r["system"])

    from statistics import mean
    sys_aggs = {}
    for s in systems:
        s_rows = [r for r in metrics if r["system"] == s]
        sys_aggs[s] = {
            "accuracy": mean(float(r["accuracy"]) for r in s_rows),
            "unsafe": mean(float(r["unsafe_allows"]) for r in s_rows),
            "cache_hit": mean(float(r["cache_hit_rate"]) for r in s_rows),
            "evals": mean(float(r["full_evaluations"]) for r in s_rows),
            "latency": mean(float(r["mean_latency_ns"]) for r in s_rows) / 1000.0,
        }

    # ------------------ PAGE 1: Title, Abstract, Results Table & Figure 1 ------------------
    p1 = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_WIDTH}" height="{PAGE_HEIGHT}" viewBox="0 0 {PAGE_WIDTH} {PAGE_HEIGHT}">',
        '  <rect width="100%" height="100%" fill="#ffffff"/>',
        '  <text x="50" y="55" font-family="-apple-system, Helvetica, Arial, sans-serif" font-size="20" font-weight="bold" fill="#0f172a">Dependency-Aware Proof Reuse for Agent Runtime Governance</text>',
        '  <text x="50" y="74" font-family="Helvetica, Arial, sans-serif" font-size="11" font-weight="bold" fill="#2563eb">Agent Runtime Research Lab v2 — Official Research Manuscript</text>',
        '  <line x1="50" y1="84" x2="545" y2="84" stroke="#cbd5e1" stroke-width="1.5"/>',
        # Abstract box
        '  <rect x="50" y="96" width="495" height="74" fill="#f8fafc" stroke="#e2e8f0" rx="4"/>',
        '  <text x="62" y="112" font-family="Helvetica, Arial, sans-serif" font-size="11" font-weight="bold" fill="#334155">ABSTRACT</text>',
        '  <text x="62" y="128" font-family="Helvetica, Arial, sans-serif" font-size="9" fill="#475569">We evaluate whether an AI agent runtime can safely reuse previously established proofs of action admissibility under</text>',
        '  <text x="62" y="141" font-family="Helvetica, Arial, sans-serif" font-size="9" fill="#475569">continuous state evolution. Comparing full recomputation against dependency-aware caching (TAPR) and negative controls,</text>',
        '  <text x="62" y="154" font-family="Helvetica, Arial, sans-serif" font-size="9" fill="#475569">On this synthetic workload, results describe only the tested implementation and scenarios; they do not establish production safety or general superiority.</text>',
        # Section 1 & Table
        '  <text x="50" y="195" font-family="Helvetica, Arial, sans-serif" font-size="13" font-weight="bold" fill="#0f172a">1. Empirical Performance &amp; Safety Table</text>',
        '  <rect x="50" y="206" width="495" height="22" fill="#1e293b" rx="2"/>',
        '  <text x="56" y="221" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#ffffff">System</text>',
        '  <text x="210" y="221" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#ffffff" text-anchor="end">Oracle Acc.</text>',
        '  <text x="290" y="221" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#ffffff" text-anchor="end">Unsafe ALLOW</text>',
        '  <text x="375" y="221" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#ffffff" text-anchor="end">Cache Hit</text>',
        '  <text x="455" y="221" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#ffffff" text-anchor="end">Full Evals</text>',
        '  <text x="535" y="221" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#ffffff" text-anchor="end">Latency (μs)</text>',
    ]

    ty = 242
    for i, s in enumerate(systems):
        agg = sys_aggs[s]
        bg = "#f1f5f9" if (i % 2 == 1) else "#ffffff"
        p1.append(f'  <rect x="50" y="{ty - 13}" width="495" height="18" fill="{bg}"/>')
        label_fmt = s.replace("_", " ").title()
        p1.append(f'  <text x="56" y="{ty}" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#1e293b">{label_fmt}</text>')
        p1.append(f'  <text x="210" y="{ty}" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#1e293b" text-anchor="end">{100*agg["accuracy"]:.1f}%</text>')
        p1.append(f'  <text x="290" y="{ty}" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#1e293b" text-anchor="end">{agg["unsafe"]:.1f}</text>')
        p1.append(f'  <text x="375" y="{ty}" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#1e293b" text-anchor="end">{100*agg["cache_hit"]:.1f}%</text>')
        p1.append(f'  <text x="455" y="{ty}" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#1e293b" text-anchor="end">{agg["evals"]:.0f}</text>')
        p1.append(f'  <text x="535" y="{ty}" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#1e293b" text-anchor="end">{agg["latency"]:.2f}</text>')
        ty += 19

    # Embed Figure 1: Decision Accuracy
    p1.append('  <text x="50" y="388" font-family="Helvetica, Arial, sans-serif" font-size="13" font-weight="bold" fill="#0f172a">2. Decision Accuracy (Oracle Agreement)</text>')
    p1.append(_img_tag(figure_dir / "decision_accuracy.png", 50, 400, 495, 205))

    # Embed Figure 2: Work Savings
    p1.append('  <text x="50" y="625" font-family="Helvetica, Arial, sans-serif" font-size="13" font-weight="bold" fill="#0f172a">3. Full Evaluations Avoided vs Recompute</text>')
    p1.append(_img_tag(figure_dir / "work_savings.png", 50, 635, 495, 175))
    p1.append('</svg>')

    # ------------------ PAGE 2: Frontier, Commit Gate, Unsafe Allows & Methodology ------------------
    p2 = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_WIDTH}" height="{PAGE_HEIGHT}" viewBox="0 0 {PAGE_WIDTH} {PAGE_HEIGHT}">',
        '  <rect width="100%" height="100%" fill="#ffffff"/>',
        '  <text x="50" y="55" font-family="Helvetica, Arial, sans-serif" font-size="16" font-weight="bold" fill="#0f172a">4. Safety-Efficiency Frontier &amp; Gate Integrity</text>',
        '  <line x1="50" y1="65" x2="545" y2="65" stroke="#cbd5e1" stroke-width="1.5"/>',
        _img_tag(figure_dir / "safety_efficiency.png", 50, 80, 495, 220),
        '  <text x="50" y="325" font-family="Helvetica, Arial, sans-serif" font-size="13" font-weight="bold" fill="#0f172a">5. Negative Controls: Unsafe ALLOW &amp; Commit Gate Aborts</text>',
        _img_tag(figure_dir / "unsafe_allows.png", 50, 340, 240, 150),
        _img_tag(figure_dir / "commit_gate.png", 305, 340, 240, 150),
        # Method / Conclusion
        '  <text x="50" y="525" font-family="Helvetica, Arial, sans-serif" font-size="13" font-weight="bold" fill="#0f172a">6. Methodology &amp; Guardrails</text>',
        '  <rect x="50" y="538" width="495" height="250" fill="#f8fafc" stroke="#e2e8f0" rx="4"/>',
        '  <text x="65" y="562" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#1e293b">• Stateful Scenarios:</text>',
        '  <text x="200" y="562" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#475569">Ten sequential families testing mid-flight revocation, path dependence, and evidence expiry.</text>',
        '  <text x="65" y="584" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#1e293b">• Invalidation Soundness:</text>',
        '  <text x="200" y="584" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#475569">TAPR keys on declared dependency vectors, matching the reference oracle on this tested workload.</text>',
        '  <text x="65" y="606" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#1e293b">• Commit boundary:</text>',
        '  <text x="200" y="606" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#475569">The benchmark gate is in-process; production race safety is not established.</text>',
        '  <text x="65" y="628" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#1e293b">• Diagnostic Controls:</text>',
        '  <text x="200" y="628" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#475569">Negative controls expose stale-decision and path-dependency failure modes in this workload.</text>',
        '  <text x="65" y="650" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#1e293b">• Layer A Extraction:</text>',
        '  <text x="200" y="650" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#475569">Natural-language extraction is outside the scope of this typed-scenario benchmark.</text>',
        '  <text x="65" y="672" font-family="Helvetica, Arial, sans-serif" font-size="9" font-weight="bold" fill="#1e293b">• Reproducibility:</text>',
        '  <text x="200" y="672" font-family="Helvetica, Arial, sans-serif" font-size="8.5" fill="#475569">Artifacts record the selected run; independent replication remains outstanding.</text>',
        '  <text x="65" y="760" font-family="Helvetica, Arial, sans-serif" font-size="8" fill="#64748b">Generated by Agent Runtime Research Lab v2 — Offline Synthetic Deterministic Systems Benchmark</text>',
        '</svg>',
    ]

    tmp_p1 = temp_dir / "page1.svg"
    tmp_p2 = temp_dir / "page2.svg"
    tmp_p1.write_text("\n".join(p1))
    tmp_p2.write_text("\n".join(p2))

    out_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["/opt/homebrew/bin/rsvg-convert", "-f", "pdf", "-o", str(out_pdf_path), str(tmp_p1), str(tmp_p2)],
        check=True,
    )
    tmp_p1.unlink(missing_ok=True)
    tmp_p2.unlink(missing_ok=True)
    return out_pdf_path


def main():
    root = Path(__file__).resolve().parents[1]
    res_pdf = root / "results/REPORT.pdf"
    build_research_paper_pdf(
        root, res_pdf, run_dir=root / "results",
        figure_dir=root / "figures", temp_dir=root / "results/pdf_tmp"
    )
    print(f"Generated research PDF: {res_pdf}")


if __name__ == "__main__":
    main()
