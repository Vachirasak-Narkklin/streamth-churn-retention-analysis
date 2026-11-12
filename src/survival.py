"""
Survival analysis of first subscriptions (run src/run_sql.py first).

    python src/survival.py

1. Kaplan-Meier survival by segment + log-rank tests
2. Proportional-hazards check on a simple Cox model
3. Time-varying Cox model (monthly plans): promo effect split into months 1-3
   vs. month 4+, unresolved support tickets counted as they happen

Unit: each customer's FIRST subscription. Win-backs are excluded here so the
clock measures one continuous stay. Annual plans are left out of the Cox model
because they can only end at day 365, which breaks the model's assumptions;
they are shown in the Kaplan-Meier table instead.

Outputs: reports/survival_km.csv, reports/survival_logrank.csv,
         reports/survival_cox.csv, reports/figures/km_promo.png,
         reports/figures/cox_hazard_ratios.png
"""
from __future__ import annotations

import warnings
from pathlib import Path

import duckdb
import matplotlib
import matplotlib.ticker
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, CoxTimeVaryingFitter, KaplanMeierFitter
from lifelines.statistics import logrank_test, multivariate_logrank_test, proportional_hazard_test

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "streamth.duckdb"
REP = ROOT / "reports"
FIG = REP / "figures"
CUTOFF = "DATE '2026-10-31'"

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#b9b8b3", "#e7e6e2"
BLUE, ORANGE = "#2a78d6", "#eb6834"

FIRST_SUBS = f"""
WITH fs AS (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY start_date) AS rn
    FROM subscriptions s),
activation AS (            -- >= 3 distinct titles in the first 7 days
    SELECT u.user_id,
           (COUNT(DISTINCT CASE WHEN v.start_ts < u.signup_date + INTERVAL 7 DAY
                                THEN v.title_id END) >= 3)::INT AS activated
    FROM users u LEFT JOIN viewing_sessions v USING (user_id)
    GROUP BY u.user_id)
SELECT f.sub_id, f.user_id, f.plan_id, f.start_date, p.billing_cycle,
       c.acquisition_channel AS channel, c.acquired_with_promo::INT AS promo,
       c.device, a.activated,
       DATEDIFF('day', f.start_date, COALESCE(f.end_date, {CUTOFF})) AS duration_days,
       (f.end_date IS NOT NULL)::INT AS event
FROM fs f
JOIN plans p USING (plan_id)
JOIN cohort_base c USING (user_id)
JOIN activation a USING (user_id)
WHERE f.rn = 1
  AND DATEDIFF('day', f.start_date, COALESCE(f.end_date, {CUTOFF})) > 0
"""

# one row per first subscription per 30-day billing period (monthly plans);
# ticket counts are taken at the end of each period, i.e. at the renewal decision
PERIODS = f"""
WITH base AS ({FIRST_SUBS}),
per AS (
    SELECT b.*, k.range AS k
    FROM base b, range(1, 40) k
    WHERE b.billing_cycle = 'monthly' AND (k.range - 1) * 30 < b.duration_days),
unresolved AS (SELECT user_id, created_at FROM support_tickets WHERE resolved_hours IS NULL)
SELECT
    per.sub_id                                        AS id,
    per.k - 1                                         AS start_m,
    LEAST(per.k * 30, per.duration_days) / 30.0       AS stop_m,
    (per.event = 1 AND per.k * 30 >= per.duration_days)::INT AS event,
    per.k, per.promo, per.activated, per.channel, per.plan_id, per.device,
    (SELECT COUNT(*) FROM unresolved t
      WHERE t.user_id = per.user_id
        AND t.created_at < per.start_date + INTERVAL (LEAST(per.k * 30, per.duration_days)) DAY) AS unres_total,
    (SELECT COUNT(*) FROM unresolved t
      WHERE t.user_id = per.user_id
        AND t.created_at <  per.start_date + INTERVAL (LEAST(per.k * 30, per.duration_days)) DAY
        AND t.created_at >= per.start_date + INTERVAL (LEAST(per.k * 30, per.duration_days) - 60) DAY) AS unres_60d
FROM per
"""

LABELS = {
    "unres_2plus": "2+ unresolved support tickets",
    "unres_recent": "Unresolved ticket in last 60 days",
    "promo_m1_3": "Promo signup — months 1–3",
    "promo_m4plus": "Promo signup — month 4+",
    "plan_id_P1": "Basic plan with ads (vs Standard)",
    "channel_social_ads": "Social ads (vs organic)",
    "channel_search": "Search (vs organic)",
    "device_smart_tv": "Smart TV (vs mobile)",
    "device_web": "Web (vs mobile)",
    "device_tablet": "Tablet (vs mobile)",
    "channel_referral": "Referral (vs organic)",
    "channel_telco_bundle": "Telco bundle (vs organic)",
    "plan_id_P3": "Premium plan (vs Standard)",
    "activated": "Activated: 3+ titles in first 7 days",
}


def km_tables(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, tests = [], []
    segs = {
        "overall": pd.Series("all", index=df.index),
        "promo": df.promo.map({1: "promo", 0: "full price"}),
        "activated": df.activated.map({1: "activated", 0: "not activated"}),
        "channel": df.channel,
        "plan": df.plan_id,
    }
    t_months = df.duration_days / 30
    for dim, labels in segs.items():
        for seg in sorted(labels.unique()):
            m = labels == seg
            kmf = KaplanMeierFitter().fit(t_months[m], df.event[m])
            rows.append({"dimension": dim, "segment": seg, "n": int(m.sum()),
                         "median_months": kmf.median_survival_time_,
                         "survival_m3": float(kmf.predict(3.0)),
                         "survival_m6": float(kmf.predict(6.0)),
                         "survival_m12": float(kmf.predict(12.0))})
        if dim != "overall":
            r = multivariate_logrank_test(t_months, labels, df.event)
            tests.append({"dimension": dim, "test_statistic": r.test_statistic,
                          "degrees_of_freedom": labels.nunique() - 1, "p_value": r.p_value})
    return pd.DataFrame(rows), pd.DataFrame(tests)


def ph_check(df: pd.DataFrame) -> pd.DataFrame:
    """Does a single hazard ratio describe promo / Basic plan over the whole lifetime?"""
    m = df[df.billing_cycle == "monthly"].assign(
        months=lambda d: d.duration_days / 30, basic=lambda d: (d.plan_id == "P1").astype(int))
    cph = CoxPHFitter().fit(m[["months", "event", "promo", "basic", "activated"]], "months", "event")
    r = proportional_hazard_test(cph, m[["months", "event", "promo", "basic", "activated"]],
                                 time_transform="rank")
    out = r.summary.reset_index().rename(columns={"index": "covariate"})
    out["hazard_ratio_single"] = out.covariate.map(np.exp(cph.params_))
    return out[["covariate", "hazard_ratio_single", "test_statistic", "p"]]


def time_varying_cox(long: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    long = long.copy()
    long["promo_m1_3"] = long.promo * (long.k <= 3)
    long["promo_m4plus"] = long.promo * (long.k > 3)
    long["unres_2plus"] = (long.unres_total >= 2).astype(int)
    long["unres_recent"] = (long.unres_60d >= 1).astype(int)
    X = pd.get_dummies(long, columns=["channel", "plan_id", "device"], dtype=int)
    X = X.drop(columns=["channel_organic", "plan_id_P2", "device_mobile",
                        "k", "promo", "unres_total", "unres_60d"])
    ctv = CoxTimeVaryingFitter().fit(X, id_col="id", start_col="start_m",
                                     stop_col="stop_m", event_col="event")
    s = ctv.summary.reset_index()
    out = pd.DataFrame({
        "covariate": s["covariate"],
        "label": s["covariate"].map(LABELS),
        "hazard_ratio": s["exp(coef)"],
        "ci_low": s["exp(coef) lower 95%"],
        "ci_high": s["exp(coef) upper 95%"],
        "p_value": s["p"],
    })
    return out.sort_values("hazard_ratio", ascending=False), ctv.log_likelihood_ratio_test().p_value


def style(ax):
    ax.set_facecolor(SURFACE)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(colors=INK2, labelsize=9, length=0)


def plot_km_promo(df: pd.DataFrame):
    m = df[df.billing_cycle == "monthly"]
    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
    style(ax)
    for seg, color, label in ((0, BLUE, "Full-price signup"), (1, ORANGE, "Promo signup")):
        d = m[m.promo == seg]
        kmf = KaplanMeierFitter().fit(d.duration_days / 30, d.event)
        sf = kmf.survival_function_.loc[:24]
        ci = kmf.confidence_interval_survival_function_.loc[:24]
        ax.fill_between(ci.index, ci.iloc[:, 0], ci.iloc[:, 1], color=color, alpha=0.15,
                        step="post", linewidth=0)
        ax.step(sf.index, sf.iloc[:, 0], where="post", color=color, linewidth=2, label=label)
        med = kmf.median_survival_time_
        ax.plot([med], [0.5], "o", color=color, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2)
        if seg == 0:
            ax.text(med + 0.3, 0.535, f"median {med:.0f} months", fontsize=9, color=INK, va="bottom")
        else:
            ax.text(med - 0.3, 0.465, f"median {med:.0f} months", fontsize=9, color=INK,
                    va="top", ha="right")
        end = float(kmf.predict(24.0))
        ax.text(24.3, end, f"{label}  {end:.0%}", fontsize=9, color=INK, va="center")
    ax.axhline(0.5, color=INK2, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.set_xlim(0, 30)
    ax.set_ylim(0, 1.02)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xlabel("Months since first subscription", color=INK2, fontsize=9.5)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(frameon=False, loc="upper right", fontsize=9, labelcolor=INK)
    fig.suptitle("Half of promo signups are gone by month 4 — two months sooner than full-price signups",
                 x=0.07, ha="left", fontsize=12.5, fontweight="bold", color=INK)
    fig.text(0.07, 0.885, "Kaplan-Meier survival of the first subscription, monthly plans, "
             "with 95% confidence bands", fontsize=9, color=INK2)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.82, bottom=0.12)
    fig.savefig(FIG / "km_promo.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


def plot_forest(cox: pd.DataFrame):
    d = cox.sort_values("hazard_ratio").reset_index(drop=True)
    sig = d.p_value < 0.05
    fig, ax = plt.subplots(figsize=(10, 6.2), facecolor=SURFACE)
    style(ax)
    y = np.arange(len(d))
    colors = [(ORANGE if hr > 1 else BLUE) if s else MUTED for hr, s in zip(d.hazard_ratio, sig)]
    ax.hlines(y, d.ci_low, d.ci_high, color=colors, linewidth=2)
    ax.scatter(d.hazard_ratio, y, color=colors, s=60, zorder=3, edgecolors=SURFACE, linewidths=2)
    for yi, (hr, s) in enumerate(zip(d.hazard_ratio, sig)):      # aligned value column
        ax.text(2.35, yi, f"{hr:.2f}×" + ("" if s else " n.s."), va="center", ha="left",
                fontsize=9, color=INK if s else INK2)
    ax.axvline(1, color=INK2, linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(d.label, fontsize=9.5, color=INK)
    ax.set_xscale("log")
    ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.set_xticks([0.5, 0.75, 1, 1.5, 2])
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:g}×"))
    ax.set_xlim(0.45, 2.9)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.text(0.97, -1.15, "lower risk  ←", ha="right", fontsize=9, color=BLUE)
    ax.text(1.03, -1.15, "→  higher risk", ha="left", fontsize=9, color=ORANGE)
    ax.set_ylim(-1.6, len(d) - 0.4)
    fig.suptitle("Unresolved tickets double churn risk; activation nearly halves it",
                 x=0.03, ha="left", fontsize=12.5, fontweight="bold", color=INK)
    fig.text(0.03, 0.905, "Hazard ratios from a time-varying Cox model, monthly plans, each factor "
             "holding the others constant. Bars = 95% CI; grey = not significant.",
             fontsize=9, color=INK2)
    fig.subplots_adjust(left=0.33, right=0.97, top=0.86, bottom=0.07)
    fig.savefig(FIG / "cox_hazard_ratios.png", dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute(FIRST_SUBS).df()
    long = con.execute(PERIODS).df()
    con.close()

    km, lr = km_tables(df)
    ph = ph_check(df)
    cox, model_p = time_varying_cox(long)
    km.round(4).to_csv(REP / "survival_km.csv", index=False)
    lr.to_csv(REP / "survival_logrank.csv", index=False)
    ph.round(4).to_csv(REP / "survival_ph_check.csv", index=False)
    cox.round(4).to_csv(REP / "survival_cox.csv", index=False)
    plot_km_promo(df)
    plot_forest(cox)

    pd.set_option("display.width", 140)
    print(f"First subscriptions: {len(df):,} | churned: {df.event.mean():.1%} | "
          f"period rows for Cox: {len(long):,}")
    print("\nKaplan-Meier:\n", km.round(3).to_string(index=False))
    print("\nLog-rank:\n", lr.to_string(index=False))
    print("\nProportional-hazards check:\n", ph.round(4).to_string(index=False))
    print(f"\nTime-varying Cox (model LR test p = {model_p:.2g}):\n",
          cox.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
