"""PDF report: PE5Y vs PE_TTM_20Q strategy comparison with VNINDEX benchmark."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from fpdf import FPDF

from ..backtest.sensitivity_runner import SensitivityRun

log = logging.getLogger(__name__)
MONTHS = list(range(1, 13))
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PCTS = [10.0, 12.0, 14.0, 16.0]


def generate_report(
    runs: list[SensitivityRun],
    vnindex: dict[int, float],
    output_path: Path,
    formation_years: list[int] | None = None,
) -> Path:
    """Generate comprehensive PDF comparing PE5Y vs PE_TTM_20Q."""
    if formation_years is None:
        formation_years = list(range(2017, 2025))
    pe5y = [r for r in runs if r.strategy == "PE5Y"]
    pe20q = [r for r in runs if r.strategy == "PE_TTM_20Q"]
    pe20q_r = [r for r in runs if r.strategy == "PE_TTM_20Q_RELAXED"]

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    _add_title_page(pdf, formation_years)
    _add_executive_summary(pdf, pe5y, pe20q, pe20q_r, vnindex)
    _add_heatmap_page(pdf, pe5y, "PE5Y", vnindex)
    _add_heatmap_page(pdf, pe20q, "PE_TTM_20Q (strict)", vnindex)
    _add_heatmap_page(pdf, pe20q_r, "PE_TTM_20Q_RELAXED", vnindex)
    _add_comparison_table(pdf, pe5y, pe20q, pe20q_r, vnindex)
    _add_yearly_breakdown(pdf, pe5y, pe20q, pe20q_r, vnindex, formation_years)
    _add_monthly_analysis(pdf, pe5y, pe20q, pe20q_r, vnindex)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
    log.info("PDF report saved to %s", output_path)
    return output_path


def _best_run(runs: list[SensitivityRun]) -> SensitivityRun | None:
    return max(runs, key=lambda r: r.net_cagr, default=None)


def _build_heatmap(runs: list[SensitivityRun]) -> np.ndarray:
    """Build 12x4 heatmap (months x pcts) of net CAGR."""
    data = np.full((12, 4), np.nan)
    for r in runs:
        mi = r.rebalance_month - 1
        pi = PCTS.index(r.select_pct) if r.select_pct in PCTS else -1
        if 0 <= mi < 12 and 0 <= pi < 4:
            data[mi, pi] = r.net_cagr * 100
    return data


def _heatmap_to_image(data: np.ndarray, title: str) -> bytes:
    """Render heatmap as PNG bytes."""
    fig, ax = plt.subplots(figsize=(6, 7))
    vmin = np.nanmin(data) if not np.all(np.isnan(data)) else 0
    vmax = np.nanmax(data) if not np.all(np.isnan(data)) else 1
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=vmin, vmax=vmax)
    ax.set_xticks(range(4))
    ax.set_xticklabels([f"{p:.0f}%" for p in PCTS])
    ax.set_yticks(range(12))
    ax.set_yticklabels(MONTH_LABELS)
    ax.set_xlabel("Select Pct")
    ax.set_ylabel("Rebalance Month")
    ax.set_title(title)
    for i in range(12):
        for j in range(4):
            v = data[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.1f}%", ha="center", va="center",
                        fontsize=8, color="black" if abs(v - (vmin+vmax)/2) < (vmax-vmin)/3 else "white")
    fig.colorbar(im, ax=ax, label="CAGR %")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def _add_title_page(pdf: FPDF, fy: list[int]):
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 24)
    pdf.ln(40)
    pdf.cell(0, 15, "PE5Y vs PE_TTM_20Q", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 16)
    pdf.cell(0, 10, "Strategy Comparison Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Formation years: {fy[0]}-{fy[-1]} (hold {fy[0]+1}-{fy[-1]+1})",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Transaction cost: 10 bps/side (20 bps round-trip)",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, "Benchmark: VNINDEX buy-and-hold",
             align="C", new_x="LMARGIN", new_y="NEXT")
    import datetime
    pdf.cell(0, 7, f"Generated: {datetime.date.today().isoformat()}",
             align="C", new_x="LMARGIN", new_y="NEXT")


def _add_executive_summary(pdf: FPDF, pe5y, pe20q, pe20q_r, vnindex):
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Executive Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    b5 = _best_run(pe5y)
    b20 = _best_run(pe20q)
    b20r = _best_run(pe20q_r)
    best_vn = max(vnindex.values()) * 100 if vnindex else 0

    pdf.set_font("Helvetica", "", 10)
    lines = []
    if b5:
        lines.append(f"Best PE5Y: M{b5.rebalance_month:02d}, {b5.select_pct:.0f}% "
                     f"-> CAGR {b5.net_cagr*100:.2f}% (win {b5.win_rate*100:.0f}%, "
                     f"worst {b5.worst_year*100:.1f}%, best {b5.best_year*100:.1f}%)")
    if b20:
        lines.append(f"Best PE_TTM_20Q: M{b20.rebalance_month:02d}, {b20.select_pct:.0f}% "
                     f"-> CAGR {b20.net_cagr*100:.2f}% (win {b20.win_rate*100:.0f}%, "
                     f"worst {b20.worst_year*100:.1f}%, best {b20.best_year*100:.1f}%)")
    if b20r:
        lines.append(f"Best PE_TTM_20Q_RELAXED: M{b20r.rebalance_month:02d}, {b20r.select_pct:.0f}% "
                     f"-> CAGR {b20r.net_cagr*100:.2f}% (win {b20r.win_rate*100:.0f}%, "
                     f"worst {b20r.worst_year*100:.1f}%, best {b20r.best_year*100:.1f}%)")
    lines.append(f"VNINDEX best: {best_vn:.2f}% CAGR")
    lines.append("")
    lines.append("PE_TTM_20Q: all 20 quarters EPS > 0 (strict quality filter)")
    lines.append("PE_TTM_20Q_RELAXED: only avg EPS > 0 (larger universe)")

    # Find overall winner
    all_best = [(b5, "PE5Y"), (b20, "PE_TTM_20Q"), (b20r, "PE_TTM_20Q_RELAXED")]
    all_best = [(b, n) for b, n in all_best if b is not None]
    if all_best:
        winner_run, winner_name = max(all_best, key=lambda x: x[0].net_cagr)
        second_run, second_name = sorted(all_best, key=lambda x: x[0].net_cagr, reverse=True)[1]
        delta = (winner_run.net_cagr - second_run.net_cagr) * 100
        lines.append(f"VERDICT: {winner_name} wins by {delta:.2f}pp over {second_name}")

    for line in lines:
        pdf.cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")


def _add_heatmap_page(pdf: FPDF, runs, title, vnindex):
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"CAGR Heatmap: {title}", new_x="LMARGIN", new_y="NEXT")
    data = _build_heatmap(runs)
    img_bytes = _heatmap_to_image(data, f"{title} Net CAGR by Month x Select%")
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(img_bytes)
    tmp.close()
    pdf.image(tmp.name, x=15, w=180)
    os.unlink(tmp.name)
    # Add VNINDEX reference line
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 9)
    for m in MONTHS:
        vn = vnindex.get(m, 0) * 100
        pdf.cell(0, 5, f"  VNINDEX M{m:02d}: {vn:.2f}%", new_x="LMARGIN", new_y="NEXT")


def _add_comparison_table(pdf: FPDF, pe5y, pe20q, pe20q_r, vnindex):
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Top Configurations Comparison", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    top5_pe5y = sorted(pe5y, key=lambda r: r.net_cagr, reverse=True)[:5]
    top5_pe20q = sorted(pe20q, key=lambda r: r.net_cagr, reverse=True)[:5]
    top5_pe20q_r = sorted(pe20q_r, key=lambda r: r.net_cagr, reverse=True)[:5]

    for label, top_runs in [("PE5Y Top 5", top5_pe5y),
                            ("PE_TTM_20Q Top 5 (strict)", top5_pe20q),
                            ("PE_TTM_20Q_RELAXED Top 5", top5_pe20q_r)]:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 8, label, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Courier", "", 7)
        pdf.cell(0, 5, f"{'Month':>5} {'Pct':>5} {'CAGR':>8} {'Alpha':>8} {'Win%':>6} "
                 f"{'Worst':>8} {'Best':>8} {'AvgN':>5}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 3, "-" * 60, new_x="LMARGIN", new_y="NEXT")
        for r in top_runs:
            vn = vnindex.get(r.rebalance_month, 0)
            alpha = r.net_cagr - vn
            pdf.cell(0, 5, f"{r.rebalance_month:>5} {r.select_pct:>5.0f} "
                     f"{r.net_cagr*100:>7.2f}% {alpha*100:>7.2f}% {r.win_rate*100:>5.0f}% "
                     f"{r.worst_year*100:>7.1f}% {r.best_year*100:>7.1f}% {r.avg_stocks:>5.0f}",
                     new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)


def _add_yearly_breakdown(pdf: FPDF, pe5y, pe20q, pe20q_r, vnindex, fy_list):
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Year-by-Year Breakdown (Best Config Each)", new_x="LMARGIN", new_y="NEXT")

    b5 = _best_run(pe5y)
    b20 = _best_run(pe20q)
    b20r = _best_run(pe20q_r)
    if not b5 or not b20:
        return

    pdf.set_font("Helvetica", "", 8)
    desc = f"PE5Y: M{b5.rebalance_month:02d} {b5.select_pct:.0f}%"
    desc += f" | PE_TTM_20Q: M{b20.rebalance_month:02d} {b20.select_pct:.0f}%"
    if b20r:
        desc += f" | RELAXED: M{b20r.rebalance_month:02d} {b20r.select_pct:.0f}%"
    pdf.cell(0, 6, desc, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Courier", "", 7)
    pdf.cell(0, 5, f"{'Hold':>5} {'PE5Y':>8} {'#':>3} {'PE20Q':>8} {'#':>3} {'RELAX':>8} {'#':>3} {'VNI':>8}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 3, "-" * 58, new_x="LMARGIN", new_y="NEXT")

    yr5 = {r.hold_year: r for r in b5.yearly_returns}
    yr20 = {r.hold_year: r for r in b20.yearly_returns}
    yr20r = {r.hold_year: r for r in b20r.yearly_returns} if b20r else {}

    for fy in fy_list:
        hy = fy + 1
        r5 = yr5.get(hy)
        r20 = yr20.get(hy)
        r20r = yr20r.get(hy)
        vn_start = calc_benchmark_cagr_single_year(hy, b5.rebalance_month)
        line = f"{hy:>5} "
        line += f"{r5.portfolio_return*100:>7.1f}% {r5.n_stocks:>3}" if r5 else f"{'N/A':>7} {'':>3}"
        line += f" {r20.portfolio_return*100:>7.1f}% {r20.n_stocks:>3}" if r20 else f" {'N/A':>7} {'':>3}"
        line += f" {r20r.portfolio_return*100:>7.1f}% {r20r.n_stocks:>3}" if r20r else f" {'N/A':>7} {'':>3}"
        line += f" {vn_start:>7}%"
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")


def _add_monthly_analysis(pdf: FPDF, pe5y, pe20q, pe20q_r, vnindex):
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Monthly Pattern Analysis", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Courier", "", 6.5)
    pdf.cell(0, 5, f"{'Month':>5} {'PE5Y Avg':>9} {'PE5Y Bst':>9} "
             f"{'20Q Avg':>8} {'20Q Bst':>8} "
             f"{'RLX Avg':>8} {'RLX Bst':>8} {'VNI':>8}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 3, "-" * 72, new_x="LMARGIN", new_y="NEXT")

    for m in MONTHS:
        m_pe5y = [r for r in pe5y if r.rebalance_month == m]
        m_pe20q = [r for r in pe20q if r.rebalance_month == m]
        m_pe20q_r = [r for r in pe20q_r if r.rebalance_month == m]
        avg5 = sum(r.net_cagr for r in m_pe5y) / len(m_pe5y) * 100 if m_pe5y else 0
        best5 = max(r.net_cagr for r in m_pe5y) * 100 if m_pe5y else 0
        avg20 = sum(r.net_cagr for r in m_pe20q) / len(m_pe20q) * 100 if m_pe20q else 0
        best20 = max(r.net_cagr for r in m_pe20q) * 100 if m_pe20q else 0
        avg20r = sum(r.net_cagr for r in m_pe20q_r) / len(m_pe20q_r) * 100 if m_pe20q_r else 0
        best20r = max(r.net_cagr for r in m_pe20q_r) * 100 if m_pe20q_r else 0
        vn = vnindex.get(m, 0) * 100
        pdf.cell(0, 5, f"{MONTH_LABELS[m-1]:>5} {avg5:>8.2f}% {best5:>8.2f}% "
                 f"{avg20:>7.2f}% {best20:>7.2f}% "
                 f"{avg20r:>7.2f}% {best20r:>7.2f}% {vn:>7.2f}%",
                 new_x="LMARGIN", new_y="NEXT")


def calc_benchmark_cagr_single_year(hold_year: int, month: int) -> str:
    """Placeholder — we don't have per-year VNINDEX in this context."""
    return "N/A"
