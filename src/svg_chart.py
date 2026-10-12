"""
svg_chart.py – Standalone Vector SVG & PNG chart renderer with collision-free labeling.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


COLORS = {
    "full_recompute":       "#2563eb",  # blue
    "tapr_dep_cache":       "#16a34a",  # emerald green
    "exact_snapshot_cache": "#9333ea",  # purple
    "coarse_cache":         "#dc2626",  # red
    "stale_proof_accept":   "#ea580c",  # orange
    "no_commit_gate":       "#db2777",  # pink
    "pathless_policy":      "#475569",  # slate
}

LABELS = {
    "full_recompute":       "Full recompute",
    "tapr_dep_cache":       "TAPR dep-cache",
    "exact_snapshot_cache": "Exact snapshot",
    "coarse_cache":         "Coarse cache ⚠",
    "stale_proof_accept":   "Stale proof ⚠",
    "no_commit_gate":       "No commit gate ⚠",
    "pathless_policy":      "Pathless policy ⚠",
}


def _convert_svg_to_png(svg_path: Path, png_path: Path) -> None:
    try:
        subprocess.run(
            ["/opt/homebrew/bin/rsvg-convert", "-f", "png", "-o", str(png_path), str(svg_path)],
            check=True,
            capture_output=True,
        )
    except Exception as e:
        print(f"Warning: rsvg-convert failed for {png_path}: {e}")


def render_bar_chart(
    systems: Sequence[str],
    values: Sequence[float],
    title: str,
    ylabel: str,
    out_png: Path,
    err: Optional[Sequence[float]] = None,
    value_fmt: str = "{:.1f}",
) -> Path:
    """Render a clean vector SVG bar chart and compile to PNG."""
    width = 720
    height = 420
    margin_left = 80
    margin_right = 40
    margin_top = 60
    margin_bottom = 90

    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    max_v = max(values) if values else 1.0
    if err:
        max_v = max(v + e for v, e in zip(values, err))
    max_y = max(1.0, max_v * 1.15)

    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'  <rect width="100%" height="100%" fill="#ffffff"/>',
        f'  <text x="{margin_left}" y="36" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="18" font-weight="bold" fill="#0f172a">{title}</text>',
    ]

    # Grid lines and Y axis
    ticks = 5
    for i in range(ticks + 1):
        y_val = (max_y / ticks) * i
        y_pos = margin_top + plot_height - (i / ticks) * plot_height
        svg_lines.append(f'  <line x1="{margin_left}" y1="{y_pos}" x2="{width - margin_right}" y2="{y_pos}" stroke="#e2e8f0" stroke-width="1"/>')
        svg_lines.append(f'  <text x="{margin_left - 10}" y="{y_pos + 4}" font-family="Helvetica, Arial, sans-serif" font-size="11" fill="#64748b" text-anchor="end">{y_val:.1f}</text>')

    # Y label
    svg_lines.append(f'  <text x="24" y="{margin_top + plot_height / 2}" font-family="Helvetica, Arial, sans-serif" font-size="12" fill="#475569" text-anchor="middle" transform="rotate(-90 24 {margin_top + plot_height / 2})">{ylabel}</text>')

    # Bars
    n = len(systems)
    bar_width = min(60, (plot_width / n) * 0.65)
    gap = plot_width / n

    for i, s in enumerate(systems):
        v = values[i]
        c = COLORS.get(s, "#64748b")
        x_center = margin_left + i * gap + gap / 2
        x_left = x_center - bar_width / 2
        bar_h = (v / max_y) * plot_height
        y_top = margin_top + plot_height - bar_h

        # Bar rect
        svg_lines.append(f'  <rect x="{x_left:.1f}" y="{y_top:.1f}" width="{bar_width:.1f}" height="{bar_h:.1f}" fill="{c}" rx="4"/>')

        # Error bar
        if err and err[i] > 0:
            e = err[i]
            e_h = (e / max_y) * plot_height
            y_hi = max(margin_top, y_top - e_h)
            y_lo = min(margin_top + plot_height, y_top + e_h)
            svg_lines.append(f'  <line x1="{x_center:.1f}" y1="{y_hi:.1f}" x2="{x_center:.1f}" y2="{y_lo:.1f}" stroke="#0f172a" stroke-width="1.5"/>')
            svg_lines.append(f'  <line x1="{x_center - 5:.1f}" y1="{y_hi:.1f}" x2="{x_center + 5:.1f}" y2="{y_hi:.1f}" stroke="#0f172a" stroke-width="1.5"/>')
            svg_lines.append(f'  <line x1="{x_center - 5:.1f}" y1="{y_lo:.1f}" x2="{x_center + 5:.1f}" y2="{y_lo:.1f}" stroke="#0f172a" stroke-width="1.5"/>')

        # Value annotation
        svg_lines.append(f'  <text x="{x_center:.1f}" y="{y_top - 8:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="11" font-weight="600" fill="#1e293b" text-anchor="middle">{value_fmt.format(v)}</text>')

        # Label rotated
        lbl = LABELS.get(s, s)
        svg_lines.append(f'  <text x="{x_center:.1f}" y="{margin_top + plot_height + 18:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="10.5" fill="#334155" text-anchor="end" transform="rotate(-30 {x_center} {margin_top + plot_height + 18})">{lbl}</text>')

    svg_lines.append('</svg>')
    svg_content = "\n".join(svg_lines)

    svg_path = out_png.with_suffix(".svg")
    svg_path.write_text(svg_content)
    _convert_svg_to_png(svg_path, out_png)
    return out_png


def render_scatter_frontier(
    systems: Sequence[str],
    hit_rates: Sequence[float],
    accuracies: Sequence[float],
    out_png: Path,
) -> Path:
    """Render the Safety vs Efficiency 2D frontier with clean, collision-free callout labels and legend."""
    width = 720
    height = 420
    ml, mr, mt, mb = 70, 210, 60, 60  # generous right margin for dedicated legend
    pw = width - ml - mr
    ph = height - mt - mb

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'  <rect width="100%" height="100%" fill="#ffffff"/>',
        f'  <text x="{ml}" y="34" font-family="-apple-system, Helvetica, Arial, sans-serif" font-size="17" font-weight="bold" fill="#0f172a">Safety vs Efficiency Frontier</text>',
        f'  <text x="{ml}" y="50" font-family="Helvetica, Arial, sans-serif" font-size="11" fill="#64748b">Top-Right represents the ideal system: High reuse (&gt;80%) &amp; 100% oracle agreement</text>',
    ]

    # Target sweet spot highlight box (top right)
    sweet_x = ml + 0.35 * pw
    sweet_y = mt
    sweet_w = 0.65 * pw
    sweet_h = 0.25 * ph
    svg.append(f'  <rect x="{sweet_x:.1f}" y="{sweet_y:.1f}" width="{sweet_w:.1f}" height="{sweet_h:.1f}" fill="#ecfdf5" rx="4"/>')
    svg.append(f'  <text x="{sweet_x + sweet_w - 8:.1f}" y="{sweet_y + 16:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="9.5" font-weight="600" fill="#059669" text-anchor="end">Target Pareto Region ★</text>')

    # Grid
    for pct in [0, 25, 50, 75, 100]:
        x = ml + (pct / 100) * pw
        y = mt + ph - (pct / 100) * ph
        svg.append(f'  <line x1="{x}" y1="{mt}" x2="{x}" y2="{mt + ph}" stroke="#e2e8f0" stroke-width="1"/>')
        svg.append(f'  <line x1="{ml}" y1="{y}" x2="{ml + pw}" y2="{y}" stroke="#e2e8f0" stroke-width="1"/>')
        svg.append(f'  <text x="{x}" y="{mt + ph + 16}" font-size="10.5" fill="#64748b" text-anchor="middle">{pct}%</text>')
        svg.append(f'  <text x="{ml - 8}" y="{y + 4}" font-size="10.5" fill="#64748b" text-anchor="end">{pct}%</text>')

    svg.append(f'  <text x="{ml + pw / 2}" y="{height - 15}" font-size="12" font-weight="600" fill="#334155" text-anchor="middle">Cache Hit Rate / Reuse (%)</text>')
    svg.append(f'  <text x="22" y="{mt + ph / 2}" font-size="12" font-weight="600" fill="#334155" text-anchor="middle" transform="rotate(-90 22 {mt + ph / 2})">Decision Accuracy (%)</text>')

    # Points with numbering & dedicated right legend
    leg_x = width - mr + 25
    leg_y = mt + 15
    svg.append(f'  <rect x="{leg_x - 10}" y="{mt}" width="{mr - 30}" height="{ph + 20}" fill="#f8fafc" stroke="#cbd5e1" rx="6"/>')
    svg.append(f'  <text x="{leg_x}" y="{leg_y}" font-family="Helvetica, Arial, sans-serif" font-size="11.5" font-weight="bold" fill="#0f172a">Runtime Systems</text>')

    # Coordinate offsets to avoid overlaps on plot
    offsets = {
        "full_recompute":       (12, -4),
        "exact_snapshot_cache": (12, 12),
        "tapr_dep_cache":       (12, -6),
        "no_commit_gate":       (12, 10),
        "pathless_policy":      (12, 4),
        "coarse_cache":         (12, -6),
        "stale_proof_accept":   (12, 10),
    }

    for i, (s, hit, acc) in enumerate(zip(systems, hit_rates, accuracies)):
        cx = ml + (hit / 100) * pw
        cy = mt + ph - (acc / 100) * ph
        col = COLORS.get(s, "#475569")
        lbl = LABELS.get(s, s)

        # Plot point circle with high-contrast numbered pin
        svg.append(f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="9" fill="{col}" stroke="#ffffff" stroke-width="2"/>')
        svg.append(f'  <text x="{cx:.1f}" y="{cy + 3.5:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="9.5" font-weight="bold" fill="#ffffff" text-anchor="middle">{i+1}</text>')

        # Legend row
        row_y = leg_y + 24 + i * 22
        svg.append(f'  <circle cx="{leg_x + 6}" cy="{row_y - 3.5}" r="7" fill="{col}"/>')
        svg.append(f'  <text x="{leg_x + 6}" y="{row_y}" font-family="Helvetica, Arial, sans-serif" font-size="8.5" font-weight="bold" fill="#ffffff" text-anchor="middle">{i+1}</text>')
        svg.append(f'  <text x="{leg_x + 20}" y="{row_y}" font-family="Helvetica, Arial, sans-serif" font-size="10.5" fill="#1e293b">{lbl}</text>')

    svg.append('</svg>')
    svg_path = out_png.with_suffix(".svg")
    svg_path.write_text("\n".join(svg))
    _convert_svg_to_png(svg_path, out_png)
    return out_png
