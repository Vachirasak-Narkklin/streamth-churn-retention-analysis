"""
Retention charts from the SQL pipeline outputs (run src/run_sql.py first).

    python src/plot_retention.py

Writes to reports/figures/:
    cohort_heatmap.png        logo retention by signup cohort x tenure month
    churn_by_tenure.png       churn rate of remaining customers by tenure month
    retention_promo.png       retention curve, promo vs full-price signups
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "streamth.duckdb"
FIG = ROOT / "reports" / "figures"

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#b9b8b3", "#e7e6e2"
BLUE, ORANGE = "#2a78d6", "#eb6834"
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=INK2, labelsize=9, length=0)


def heatmap(con):
    df = con.execute("""
        SELECT strftime(cohort_month, '%Y-%m') AS cohort, k, logo_retention
        FROM retention_matrix WHERE k <= 24 ORDER BY cohort_month, k""").df()
    grid = df.pivot(index="cohort", columns="k", values="logo_retention")
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    cmap.set_bad(SURFACE)

    fig, ax = plt.subplots(figsize=(11, 9), facecolor=SURFACE)
    style(ax)
    im = ax.imshow(grid.values, cmap=cmap, vmin=0.2, vmax=1.0, aspect="auto")
    # thin surface gaps between cells
    ax.set_xticks(np.arange(-0.5, grid.shape[1], 1), minor=True)
    ax.set_yticks(np.arange(-0.5, grid.shape[0], 1), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=1.5)
    ax.tick_params(which="minor", length=0)
    ax.set_xticks(range(0, 25, 3))
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels(grid.index, fontsize=8)
    ax.set_xlabel("Tenure month (30-day billing periods since signup)", color=INK2, fontsize=9.5)
    ax.set_ylabel("Signup cohort", color=INK2, fontsize=9.5)
    # direct labels only on the two columns the story is about
    for col in (3, 12):
        for r, v in enumerate(grid[col].values):
            if not np.isnan(v):
                ax.text(col, r, f"{v:.0%}", ha="center", va="center", fontsize=6.5,
                        color="white" if v < 0.62 else INK)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=8, colors=INK2, length=0)
    cb.ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    cb.set_label("Customers still subscribed", color=INK2, fontsize=9)
    fig.suptitle("Every cohort loses about a third of its customers by month 3",
                 x=0.06, ha="left", fontsize=13, fontweight="bold", color=INK)
    fig.text(0.06, 0.935, "Share of each signup cohort still paying at each tenure month. "
             "Labels on months 3 and 12. Blank = not yet observable.",
             fontsize=9, color=INK2)
    fig.subplots_adjust(left=0.1, right=0.97, top=0.92, bottom=0.06)
    fig.savefig(FIG / "cohort_heatmap.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


def churn_by_tenure(con):
    df = con.execute("""
        WITH l AS (
            SELECT k, active_customers AS act,
                   LAG(active_customers) OVER (PARTITION BY cohort_month ORDER BY k) AS prev
            FROM retention_matrix)
        SELECT k, SUM(prev - act) * 1.0 / SUM(prev) AS churn
        FROM l WHERE k BETWEEN 1 AND 18 GROUP BY k ORDER BY k""").df()
    colors = [BLUE if k in (1, 2, 3) or k == 13 else MUTED for k in df.k]
    fig, ax = plt.subplots(figsize=(10, 4.8), facecolor=SURFACE)
    style(ax)
    bars = ax.bar(df.k, df.churn, color=colors, width=0.7, edgecolor=SURFACE, linewidth=2)
    for b, k, v in zip(bars, df.k, df.churn):
        if k in (1, 2, 3, 4, 13):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.003, f"{v:.1%}", ha="center",
                    va="bottom", fontsize=9, color=INK)
    ax.annotate("Annual plans\nrenew here", xy=(13, df.loc[df.k == 13, "churn"].item()),
                xytext=(15.2, 0.11), fontsize=9, color=INK2,
                arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
    ax.set_xticks(df.k)
    ax.set_xlabel("Tenure month", color=INK2, fontsize=9.5)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylim(0, 0.18)
    ax.set_yticks([0, 0.03, 0.06, 0.09, 0.12, 0.15, 0.18])
    fig.suptitle("Churn climbs until month 3, then drops by more than half",
                 x=0.07, ha="left", fontsize=13, fontweight="bold", color=INK)
    fig.text(0.07, 0.885, "Share of customers active last month who are no longer subscribed "
             "this month, all cohorts combined", fontsize=9, color=INK2)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.82, bottom=0.12)
    fig.savefig(FIG / "churn_by_tenure.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


def promo_curves(con):
    df = con.execute("""
        SELECT CASE WHEN c.acquired_with_promo THEN 'Promo signup' ELSE 'Full-price signup' END AS seg,
               t.k, AVG(CASE WHEN t.is_active THEN 1.0 ELSE 0 END) AS ret, COUNT(*) AS n
        FROM tenure_grid t JOIN cohort_base c USING (user_id)
        WHERE t.k <= 18 GROUP BY 1, 2 ORDER BY 1, 2""").df()
    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
    style(ax)
    for seg, color in (("Full-price signup", BLUE), ("Promo signup", ORANGE)):
        d = df[df.seg == seg]
        ax.plot(d.k, d.ret, color=color, linewidth=2, label=seg)
        ax.scatter(d.k, d.ret, color=color, s=22, zorder=3, edgecolors=SURFACE, linewidths=1.5)
        end = d.iloc[-1]
        ax.text(end.k + 0.3, end.ret, f"{seg}  {end.ret:.0%}", va="center", fontsize=9, color=INK)
        m3 = d[d.k == 3].ret.item()
        ax.text(3.25, m3 + (0.012 if seg.startswith("Full") else -0.012), f"{m3:.0%}",
                ha="left", va="bottom" if seg.startswith("Full") else "top", fontsize=9.5, color=INK)
    ax.axvline(3, color=INK2, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.text(3.1, 1.0, "month 3", fontsize=8.5, color=INK2, va="top")
    ax.set_xticks(range(0, 19, 3))
    ax.set_xlim(0, 22.5)
    ax.set_ylim(0.3, 1.02)
    ax.set_xlabel("Tenure month", color=INK2, fontsize=9.5)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(frameon=False, loc="upper right", fontsize=9, labelcolor=INK)
    fig.suptitle("Discount signups trail full-price signups by ~10 points at month 3",
                 x=0.07, ha="left", fontsize=13, fontweight="bold", color=INK)
    fig.text(0.07, 0.885, "Share still subscribed, by whether the customer joined with a "
             "first-months discount", fontsize=9, color=INK2)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.82, bottom=0.12)
    fig.savefig(FIG / "retention_promo.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    heatmap(con)
    churn_by_tenure(con)
    promo_curves(con)
    print("Saved figures to", FIG)


if __name__ == "__main__":
    main()
