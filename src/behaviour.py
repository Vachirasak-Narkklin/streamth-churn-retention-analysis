"""
Behavioural drivers of churn (run src/run_sql.py first).

    python src/behaviour.py

1. Usage before a cancel request vs. customers who renew
2. Warning signals 21 days before a renewal: churn rate, lift, coverage
3. Content mix: do churners watch different things?
4. Exit survey: who leaves for a shared account?

Outputs: reports/behaviour_*.csv and three charts in reports/figures/.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "streamth.duckdb"
REP = ROOT / "reports"
FIG = REP / "figures"

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#b9b8b3", "#e7e6e2"
BLUE, ORANGE = "#2a78d6", "#eb6834"

SIGNALS = {
    "14+ days since last viewing": "days_since_last_view >= 14",
    "Viewing fell by half or more": "usage_ratio_14d < 0.5",
    "One title ≥ 50% of recent viewing": "top_title_share >= 0.5 AND sessions_30d >= 4",
    "2+ unresolved support tickets": "unresolved_total >= 2",
    "Unresolved ticket in last 60 days": "unresolved_60d >= 1",
}


def style(ax):
    ax.set_facecolor(SURFACE)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(colors=INK2, labelsize=9, length=0)


# ------------------------------------------------------------- 1. usage curve
def usage_before_cancel(con) -> pd.DataFrame:
    """Weekly sessions in the 12 weeks before an anchor date, indexed to weeks -12..-9.
    Churners: anchor = cancel request date (exit survey). Comparison: customers who
    renewed, anchored 10 days before a renewal (the average cancel lead)."""
    return con.execute("""
        WITH anchors AS (
            SELECT e.user_id, CAST(e.cancel_date AS DATE) AS anchor, s.start_date, 'Cancelled' AS grp
            FROM exit_survey e JOIN subscriptions s USING (sub_id)
            UNION ALL
            SELECT r.user_id, CAST(r.renewal_day - INTERVAL 10 DAY AS DATE), s.start_date, 'Renewed'
            FROM renewal_snapshots r JOIN subscriptions s USING (sub_id)
            WHERE NOT r.churned AND NOT r.ended_involuntary AND hash(r.sub_id || r.k) % 20 = 0
        ),
        eligible AS (                -- need 12 full weeks of history inside the subscription
            SELECT * FROM anchors WHERE anchor - INTERVAL 84 DAY >= start_date
        ),
        weekly AS (
            SELECT a.grp, a.user_id, a.anchor,
                   -- days 1-7 before the anchor = week -1, days 8-14 = week -2, ...
                   -(FLOOR((DATEDIFF('day', CAST(v.start_ts AS DATE), a.anchor) - 1) / 7) + 1) AS week,
                   COUNT(*) AS n
            FROM eligible a
            JOIN viewing_sessions v
              ON v.user_id = a.user_id
             AND CAST(v.start_ts AS DATE) >= a.anchor - INTERVAL 84 DAY
             AND CAST(v.start_ts AS DATE) <  a.anchor
            GROUP BY 1, 2, 3, 4
        ),
        grid AS (
            SELECT a.grp, a.user_id, a.anchor, w.range AS week
            FROM eligible a, range(-12, 0) w
        )
        SELECT g.grp, g.week, AVG(COALESCE(w.n, 0)) AS sessions, COUNT(*) AS customers
        FROM grid g
        LEFT JOIN weekly w USING (grp, user_id, anchor, week)
        GROUP BY 1, 2 ORDER BY 1, 2""").df()


def plot_usage(u: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
    style(ax)
    out = []
    for grp, color in (("Renewed", BLUE), ("Cancelled", ORANGE)):
        d = u[u.grp == grp].sort_values("week")
        base = d[d.week <= -9].sessions.mean()
        idx = d.sessions / base
        out.append(d.assign(index=idx))
        ax.plot(d.week, idx, color=color, linewidth=2)
        ax.scatter(d.week, idx, color=color, s=24, zorder=3, edgecolors=SURFACE, linewidths=1.5)
        ax.text(-0.75, idx.iloc[-1], f"{grp}  {idx.iloc[-1]:.0%}", va="center", fontsize=9.5, color=INK)
    ax.axvspan(-4.5, -0.5, color=GRID, alpha=0.5, linewidth=0)
    ax.text(-4.4, 1.17, "last 4 weeks before\nthe cancel request", fontsize=8.5, color=INK2, va="top")
    ax.set_xticks(range(-12, 0))
    ax.set_xticklabels([f"{w}" for w in range(-12, 0)])
    ax.set_xlim(-12.4, 1.6)
    ax.set_ylim(0.3, 1.2)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel("Weeks before the cancel request (or the matched date for renewers)",
                  color=INK2, fontsize=9.5)
    fig.suptitle("Viewing starts falling about four weeks before customers cancel",
                 x=0.07, ha="left", fontsize=12.5, fontweight="bold", color=INK)
    fig.text(0.07, 0.885, "Weekly viewing sessions, indexed to each group's average 9–12 weeks "
             "before (= 100%)", fontsize=9, color=INK2)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.82, bottom=0.12)
    fig.savefig(FIG / "usage_before_cancel.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)
    return pd.concat(out)


# -------------------------------------------------------------- 2. signals
def signal_table(con) -> pd.DataFrame:
    rows = []
    for name, cond in SIGNALS.items():
        for band, bcond in (("Months 1–3", "k <= 3"), ("Month 4+", "k >= 4")):
            r = con.execute(f"""
                SELECT
                    AVG(CASE WHEN {cond} THEN 1.0 ELSE 0 END)                         AS flagged_share,
                    AVG(CASE WHEN {cond} THEN churned::INT END)                       AS churn_flagged,
                    AVG(CASE WHEN NOT ({cond}) OR ({cond}) IS NULL THEN churned::INT END) AS churn_other,
                    SUM(CASE WHEN {cond} AND churned THEN 1 ELSE 0 END) * 1.0
                        / SUM(churned::INT)                                           AS churners_caught,
                    COUNT(*)                                                          AS renewals
                FROM renewal_snapshots
                WHERE NOT ended_involuntary AND {bcond}""").fetchone()
            rows.append({"signal": name, "tenure": band, "renewals": r[4], "flagged_share": r[0],
                         "churn_if_flagged": r[1], "churn_otherwise": r[2],
                         "lift": r[1] / r[2], "share_of_churners_flagged": r[3]})
    return pd.DataFrame(rows)


def plot_signals(t: pd.DataFrame):
    order = (t[t.tenure == "Month 4+"].sort_values("lift").signal.tolist())
    fig, ax = plt.subplots(figsize=(10, 5.2), facecolor=SURFACE)
    style(ax)
    y = {s: i for i, s in enumerate(order)}
    for band, color, off in (("Months 1–3", ORANGE, 0.14), ("Month 4+", BLUE, -0.14)):
        d = t[t.tenure == band]
        ys = [y[s] + off for s in d.signal]
        ax.hlines(ys, 1, d.lift, color=color, linewidth=2)
        ax.scatter(d.lift, ys, color=color, s=50, zorder=3, edgecolors=SURFACE, linewidths=1.5,
                   label=band)
        for yy, v in zip(ys, d.lift):
            ax.text(v + 0.04, yy, f"{v:.1f}×", va="center", fontsize=8.5, color=INK)
    ax.axvline(1, color=INK2, linewidth=1)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(order, fontsize=9.5, color=INK)
    ax.set_xlim(0.8, t.lift.max() * 1.15)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:g}×"))
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.legend(*ax.get_legend_handles_labels(), frameon=False, loc="upper left", ncol=2,
               bbox_to_anchor=(0.29, 0.86), fontsize=9, labelcolor=INK)
    ax.set_xlabel("Churn rate when the signal is present ÷ churn rate when it is not",
                  color=INK2, fontsize=9.5)
    fig.suptitle("Three weeks before a renewal, silence and bingeing already flag likely leavers",
                 x=0.03, ha="left", fontsize=12.5, fontweight="bold", color=INK)
    fig.text(0.03, 0.885, "Signals measured 21 days before each monthly renewal; compared within "
             "the same tenure band", fontsize=9, color=INK2)
    fig.subplots_adjust(left=0.29, right=0.97, top=0.78, bottom=0.12)
    fig.savefig(FIG / "warning_signal_lift.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


# -------------------------------------------------------------- 3. content
def content_mix(con) -> pd.DataFrame:
    """Share of viewing minutes by origin / genre in the 30 days before the snapshot."""
    return con.execute("""
        WITH v AS (
            SELECT r.churned, t.origin, t.genre, t.is_original, vs.minutes
            FROM renewal_snapshots r
            JOIN viewing_sessions vs
              ON vs.user_id = r.user_id
             AND vs.start_ts <  r.snapshot_day
             AND vs.start_ts >= r.snapshot_day - INTERVAL 30 DAY
            JOIN titles t USING (title_id)
            WHERE NOT r.ended_involuntary AND hash(r.sub_id || r.k) % 4 = 0
        ),
        long AS (
            SELECT churned, 'origin' AS dim, origin AS value, minutes FROM v
            UNION ALL SELECT churned, 'genre', genre, minutes FROM v
            UNION ALL SELECT churned, 'original', CASE WHEN is_original THEN 'StreamTH original'
                                                       ELSE 'licensed' END, minutes FROM v
        ),
        agg AS (
            SELECT dim, value, churned, SUM(minutes) AS m FROM long GROUP BY 1, 2, 3
        )
        SELECT dim, value,
               SUM(CASE WHEN churned THEN m END) / SUM(SUM(CASE WHEN churned THEN m END)) OVER (PARTITION BY dim)
                   AS share_churned,
               SUM(CASE WHEN NOT churned THEN m END) / SUM(SUM(CASE WHEN NOT churned THEN m END)) OVER (PARTITION BY dim)
                   AS share_renewed
        FROM agg GROUP BY dim, value ORDER BY dim, share_renewed DESC""").df().assign(
        diff_pp=lambda d: (d.share_churned - d.share_renewed) * 100)


# ---------------------------------------------------------- 4. shared account
def shared_account(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    seg = con.execute("""
        WITH e AS (
            SELECT e.*, s.plan_id, u.age_band,
                   u.acquired_with_promo AND s.start_date = u.signup_date AS promo_first_sub,
                   (e.reason = 'joined_shared_account')::INT AS shared
            FROM exit_survey e
            JOIN subscriptions s USING (sub_id)
            JOIN users u ON u.user_id = e.user_id)
        SELECT 'Plan' AS dimension,
               CASE plan_id WHEN 'P1' THEN 'Basic with ads' WHEN 'P2' THEN 'Standard'
                            WHEN 'P3' THEN 'Premium' ELSE 'Annual' END AS segment,
               COUNT(*) AS answers, AVG(shared) AS shared_share FROM e GROUP BY 1, 2
        UNION ALL
        SELECT 'Signup', CASE WHEN promo_first_sub THEN 'Promo signup' ELSE 'Full price / win-back' END,
               COUNT(*), AVG(shared) FROM e GROUP BY 1, 2
        UNION ALL
        SELECT 'Age', age_band, COUNT(*), AVG(shared) FROM e GROUP BY 1, 2
        ORDER BY 1, 2""").df()
    reasons = con.execute("""
        SELECT reason, COUNT(*) AS answers, COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () AS share
        FROM exit_survey GROUP BY 1 ORDER BY 2 DESC""").df()
    return seg, reasons


def plot_shared(seg: pd.DataFrame, overall: float):
    order = [("Plan", "Premium"), ("Plan", "Standard"), ("Plan", "Basic with ads"),
             ("Signup", "Full price / win-back"), ("Signup", "Promo signup"),
             ("Age", "55+"), ("Age", "45-54"), ("Age", "35-44"), ("Age", "25-34"), ("Age", "18-24")]
    d = seg.set_index(["dimension", "segment"]).loc[order].reset_index()
    labels = [f"{s}" for _, s in order]
    ypos, y, prev = [], 0, None
    for dim, _ in order:            # gap between dimension groups
        if prev is not None and dim != prev:
            y += 0.6
        ypos.append(y)
        y += 1
        prev = dim
    fig, ax = plt.subplots(figsize=(10, 5.6), facecolor=SURFACE)
    style(ax)
    colors = [ORANGE if v >= overall * 1.3 else MUTED for v in d.shared_share]
    ax.barh(ypos, d.shared_share, color=colors, height=0.7, edgecolor=SURFACE, linewidth=2)
    for yy, v in zip(ypos, d.shared_share):
        ax.text(v + 0.003, yy, f"{v:.0%}", va="center", fontsize=9, color=INK)
    ax.axvline(overall, color=INK2, linewidth=1, linestyle=(0, (3, 3)))
    ax.text(overall + 0.002, max(ypos) + 0.75, f"all answers {overall:.1%}", fontsize=8.5, color=INK2)
    ax.set_yticks(ypos)
    ax.set_yticklabels(labels, fontsize=9.5, color=INK)
    for dim in ("Plan", "Signup", "Age"):
        ys = [p for p, (dd, _) in zip(ypos, order) if dd == dim]
        ax.text(-0.003, max(ys) + 0.62, dim.upper(), fontsize=8, color=INK2, ha="right",
                transform=ax.get_yaxis_transform())
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.set_xticks([0, 0.05, 0.10, 0.15, 0.20])
    ax.set_xlim(0, 0.205)
    ax.set_ylim(-0.6, max(ypos) + 1.2)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.suptitle("Basic-plan and younger customers are the most likely to leave for a shared account",
                 x=0.03, ha="left", fontsize=12.5, fontweight="bold", color=INK)
    fig.text(0.03, 0.89, "Share of exit-survey answers that were “joined a shared account”",
             fontsize=9, color=INK2)
    fig.subplots_adjust(left=0.2, right=0.97, top=0.84, bottom=0.07)
    fig.savefig(FIG / "shared_account_reason.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)

    u = usage_before_cancel(con)
    u_idx = plot_usage(u)
    sig = signal_table(con)
    plot_signals(sig)
    content = content_mix(con)
    seg, reasons = shared_account(con)
    overall = reasons.set_index("reason").loc["joined_shared_account", "share"]
    plot_shared(seg, overall)
    where = con.execute("""SELECT switched_to, COUNT(*) n, COUNT(*) * 1.0 / SUM(COUNT(*)) OVER () share
                           FROM exit_survey WHERE reason = 'joined_shared_account'
                           GROUP BY 1 ORDER BY 2 DESC""").df()
    con.close()

    u_idx.round(4).to_csv(REP / "behaviour_usage_curve.csv", index=False)
    sig.round(4).to_csv(REP / "behaviour_signals.csv", index=False)
    content.round(4).to_csv(REP / "behaviour_content_mix.csv", index=False)
    seg.round(4).to_csv(REP / "behaviour_shared_account.csv", index=False)
    reasons.round(4).to_csv(REP / "exit_reasons.csv", index=False)

    pd.set_option("display.width", 160)
    print("Usage index (week -1):",
          u_idx[u_idx.week == -1][["grp", "index", "customers"]].round(3).to_string(index=False))
    print(u_idx.pivot(index="week", columns="grp", values="index").round(3).to_string())
    print("\nSignals:\n", sig.round(3).to_string(index=False))
    print("\nContent mix (largest gaps):\n",
          content.reindex(content.diff_pp.abs().sort_values(ascending=False).index).head(8)
          .round(3).to_string(index=False))
    print("\nExit reasons:\n", reasons.round(3).to_string(index=False))
    print("\nShared account by segment:\n", seg.round(3).to_string(index=False))
    print("\nShared account destination:\n", where.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
