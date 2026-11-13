"""
StreamTH synthetic data generator
=================================
Generates a realistic, fully synthetic subscription-streaming dataset for a
fictional Thai SVOD platform "StreamTH". No real company data is used.

Simulation is a discrete-time renewal model: at the end of every billing
period each subscriber either renews, cancels (voluntary) or fails payment
(involuntary). Hazards are driven by tenure plus behavioural multipliers that
encode the "planted patterns" documented in docs/simulation_assumptions.md.

Outputs (data/raw/*.csv):
    users, plans, subscriptions, payments, viewing_sessions, titles,
    support_tickets, exit_survey
Ground-truth latent variables (for validation only, NOT for analysis) are
written to data/simulation/ground_truth_users.csv.

Run:  python src/generate_data.py
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_USERS = 50_000
N_TITLES = 1_500
START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2026-10-31")          # observation cut-off (censoring date)
HORIZON = (END - START).days               # last observable day index
MONTH_DAYS, YEAR_DAYS = 30, 365

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SIM = ROOT / "data" / "simulation"

rng = np.random.default_rng(SEED)

# ---------------------------------------------------------------- reference
PLANS = pd.DataFrame(
    [
        ("P1", "Basic (with ads)", 99, "monthly", True),
        ("P2", "Standard", 219, "monthly", False),
        ("P3", "Premium", 349, "monthly", False),
        ("P4", "Standard Annual", 2190, "annual", False),
    ],
    columns=["plan_id", "name", "price_thb", "billing_cycle", "has_ads"],
)
PLAN_P = [0.30, 0.38, 0.20, 0.12]
PLAN_HAZARD = {"P1": 1.15, "P2": 1.00, "P3": 0.85, "P4": 1.00}

CHANNELS = ["organic", "social_ads", "search", "referral", "telco_bundle"]
CHANNEL_P = [0.30, 0.25, 0.15, 0.12, 0.18]
CHANNEL_PROMO_P = {"organic": .15, "social_ads": .55, "search": .30,
                   "referral": .35, "telco_bundle": .10}
CHANNEL_HAZARD = {"organic": 1.0, "social_ads": 1.15, "search": 1.0,
                  "referral": 0.9, "telco_bundle": 0.8}

REGIONS = ["Bangkok & Vicinity", "Central", "North", "Northeast", "South", "East"]
REGION_P = [0.42, 0.14, 0.12, 0.16, 0.10, 0.06]
AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55+"]
AGE_P = [0.24, 0.34, 0.22, 0.12, 0.08]
DEVICES = ["mobile", "smart_tv", "web", "tablet"]
DEVICE_P = [0.48, 0.30, 0.14, 0.08]

# Tenure hazard of voluntary churn at the end of billing period k (monthly).
# Pattern 1: peak at period 3, lower after period 6.
BASE_HAZARD = {1: .075, 2: .085, 3: .150, 4: .085, 5: .065, 6: .055}
LATE_HAZARD = .035
ANNUAL_RENEWAL_CHURN = .22

PAY_FAIL_P = {"P1": .036, "P2": .025, "P3": .022, "P4": .035}
PAY_RECOVER_P = .35
WINBACK_P = .15

TICKET_CATS = ["billing", "playback", "login", "content_request", "subtitle"]
TICKET_CAT_P = [0.28, 0.30, 0.17, 0.15, 0.10]
COMPETITORS = ["Netflix", "Prime Video", "Disney+", "iQIYI/WeTV", "Viu", "Other"]


# ------------------------------------------------------------------ titles
def make_titles() -> pd.DataFrame:
    genres = ["drama", "romance", "comedy", "action", "thriller", "horror",
              "fantasy", "documentary", "anime", "variety"]
    origins = ["KR", "TH", "US", "CN", "JP"]
    origin = rng.choice(origins, N_TITLES, p=[.30, .20, .25, .15, .10])
    genre = rng.choice(genres, N_TITLES, p=[.20, .15, .12, .12, .10, .07, .08, .05, .06, .05])
    genre = np.where(origin == "JP", np.where(rng.random(N_TITLES) < .6, "anime", genre), genre)
    content_type = np.where(rng.random(N_TITLES) < .62, "series", "movie")
    episodes = np.where(content_type == "series", rng.choice([8, 10, 12, 16, 20, 24], N_TITLES), 1)
    # 70% catalogue titles released before launch, 30% released during the period
    back = START - pd.to_timedelta(rng.integers(30, 365 * 8, N_TITLES), unit="D")
    new = START + pd.to_timedelta(rng.integers(0, HORIZON, N_TITLES), unit="D")
    release = np.where(rng.random(N_TITLES) < .70, back, new)
    df = pd.DataFrame({
        "title_id": [f"T{i:04d}" for i in range(1, N_TITLES + 1)],
        "genre": genre, "origin": origin,
        "is_original": rng.random(N_TITLES) < .10,
        "content_type": content_type, "episodes": episodes,
        "release_date": pd.to_datetime(release).normalize(),
    })
    return df


# ---------------------------------------------------------------- users
def make_users() -> pd.DataFrame:
    months = pd.period_range(START, END, freq="M")
    m = np.arange(len(months))
    w = 1 + 0.04 * m                                   # business growth
    w *= np.where(np.isin(months.month, [4, 12]), 1.5, 1.0)  # Songkran / year-end campaigns
    w *= rng.normal(1, .08, len(m)).clip(.8, 1.2)
    sm = rng.choice(len(months), N_USERS, p=w / w.sum())
    starts = months[sm].to_timestamp()
    dim = months[sm].days_in_month.to_numpy()
    day = rng.integers(0, dim)
    signup = starts + pd.to_timedelta(day, unit="D")
    channel = rng.choice(CHANNELS, N_USERS, p=CHANNEL_P)
    promo = rng.random(N_USERS) < np.vectorize(CHANNEL_PROMO_P.get)(channel)
    plan = rng.choice(PLANS.plan_id, N_USERS, p=PLAN_P)
    # Telco-bundle users skew to Basic plan
    swap = (channel == "telco_bundle") & (rng.random(N_USERS) < .35)
    plan = np.where(swap, "P1", plan)
    return pd.DataFrame({
        "user_id": [f"U{i:06d}" for i in range(1, N_USERS + 1)],
        "signup_date": signup.normalize(),
        "age_band": rng.choice(AGE_BANDS, N_USERS, p=AGE_P),
        "region": rng.choice(REGIONS, N_USERS, p=REGION_P),
        "device": rng.choice(DEVICES, N_USERS, p=DEVICE_P),
        "acquisition_channel": channel,
        "acquired_with_promo": promo,
        "_plan": plan,
    })


# ---------------------------------------------------------- simulation core
class Sink:
    """Row collectors (lists of tuples) – faster than growing DataFrames."""
    def __init__(self):
        self.subs, self.pays, self.tickets, self.survey = [], [], [], []
        self.sess_user, self.sess_day, self.sess_title, self.sess_binge = [], [], [], []
        self.truth = []
        self.sub_seq = self.pay_seq = self.tk_seq = 0


def pick_titles(n, t_day, pop_p, rel_day):
    """Popularity-weighted titles released on/before t_day (rejection sampling)."""
    out = rng.choice(N_TITLES, n, p=pop_p)
    bad = rel_day[out] > t_day
    tries = 0
    while bad.any() and tries < 20:
        out[bad] = rng.choice(N_TITLES, bad.sum(), p=pop_p)
        bad = rel_day[out] > t_day
        tries += 1
    return out[~bad]


def sessions_for_window(sink, uid, s, e, lam, cancel_day, decays, dip, binge):
    """Inhomogeneous Poisson process of viewing sessions via thinning."""
    if e <= s:
        return
    lam_max = lam * (3.2 if binge else 1.0)
    n = rng.poisson(lam_max * (e - s))
    if n == 0:
        return
    t = np.sort(rng.uniform(s, e, n))
    f = np.ones(n)
    if cancel_day is not None and decays:
        # Pattern 4: usage decays during the 28 days before the cancel request
        w = (t >= cancel_day - 28) & (t < cancel_day)
        f[w] = 1 - 0.85 * (t[w] - (cancel_day - 28)) / 28
        f[t >= cancel_day] = 0.10
    if dip is not None:                               # noise: temporary dips
        f[(t >= dip) & (t < dip + 30)] *= 0.4
    is_binge = np.zeros(n, bool)
    if binge:
        b0, b1 = binge
        in_b = (t >= b0) & (t < b1)
        f[in_b] *= 3.2
        is_binge = in_b & (rng.random(n) < .75)
    keep = rng.random(n) < f / (3.2 if binge else 1.0)
    t, is_binge = t[keep], is_binge[keep]
    sink.sess_user.append(np.full(len(t), uid))
    sink.sess_day.append(t)
    sink.sess_binge.append(is_binge)


def simulate(users, titles):
    sink = Sink()
    rel_day = (titles.release_date - START).dt.days.to_numpy()
    pop = 1 / np.arange(1, N_TITLES + 1) ** 0.9
    pop_p = rng.permutation(pop / pop.sum())
    series_idx = np.where(titles.content_type == "series")[0]
    price = dict(zip(PLANS.plan_id, PLANS.price_thb))

    U = users.to_dict("records")
    for i, u in enumerate(U):
        uid = i
        sday = (u["signup_date"] - START).days
        eng = rng.normal(0, .6)
        hz_user = rng.lognormal(0, .3)
        lam = 0.17 * np.exp(eng)
        titles_first7 = rng.poisson(np.exp(eng) * 2.0)
        activated = titles_first7 >= 3
        binge_user = rng.random() < .12
        binge_period = rng.choice([1, 2, 3], p=[.40, .35, .25]) if binge_user else None
        decays = rng.random() < .75                  # 25% of churners give no warning
        dip = sday + rng.integers(30, 400) if rng.random() < .20 else None
        promo, plan, ch = u["acquired_with_promo"], u["_plan"], u["acquisition_channel"]

        # first-7-day exploration sessions (activation behaviour)
        if titles_first7 > 0:
            cand = rng.choice(N_TITLES, titles_first7 * 3, p=pop_p)
            cand = pd.unique(cand[rel_day[cand] <= sday])[:titles_first7]
            if len(cand):
                d = np.sort(rng.uniform(sday, min(sday + 7, HORIZON + 1), len(cand)))
                sink.sess_user.append(np.full(len(cand), uid))
                sink.sess_day.append(d)
                sink.sess_binge.append(np.zeros(len(cand), bool))
                sink.sess_title.append((len(sink.sess_day) - 1, cand))

        n_unresolved, last_unresolved = 0, None
        sub_start, attempt, winback_done = sday, 1, False
        while sub_start <= HORIZON:
            sink.sub_seq += 1
            sub_id = sink.sub_seq
            period_len = YEAR_DAYS if plan == "P4" else MONTH_DAYS
            k, status, cancel_type, end_day, cancel_day = 0, "active", None, None, None
            binge_win, binge_title = None, None
            while True:
                k += 1
                p0 = sub_start + (k - 1) * period_len
                p1 = p0 + period_len
                if p0 > HORIZON:
                    break
                # ---- payment at period start
                disc = 1.0
                if promo and attempt == 1:
                    if plan == "P4" and k == 1:
                        disc = 0.8
                    elif plan != "P4" and k <= 2:
                        disc = 0.5
                amount = round(price[plan] * disc, 2)
                if k > 1 and rng.random() < PAY_FAIL_P[plan]:
                    sink.pay_seq += 1
                    sink.pays.append((sink.pay_seq, sub_id, p0, amount, "failed"))
                    if rng.random() < PAY_RECOVER_P:
                        sink.pay_seq += 1
                        sink.pays.append((sink.pay_seq, sub_id, p0 + rng.integers(1, 4), amount, "success"))
                    else:
                        status, cancel_type, end_day = "payment_failed", "involuntary", p0
                        break
                else:
                    sink.pay_seq += 1
                    sink.pays.append((sink.pay_seq, sub_id, p0, amount, "success"))
                # ---- support tickets during the period
                nt = rng.poisson(0.12 * (1.5 if u["device"] == "smart_tv" else 1.0)
                                 * (period_len / MONTH_DAYS) ** .5)
                for _ in range(nt):
                    td = p0 + rng.uniform(0, period_len)
                    if td > HORIZON:
                        continue
                    unresolved = rng.random() < .22
                    sink.tk_seq += 1
                    sink.tickets.append((sink.tk_seq, uid, td,
                                         rng.choice(TICKET_CATS, p=TICKET_CAT_P),
                                         np.nan if unresolved else round(rng.lognormal(2.8, .8), 1)))
                    n_unresolved += unresolved
                    if unresolved:
                        last_unresolved = td
                # ---- binge window (pattern 6)
                if binge_user and attempt == 1 and k == binge_period and plan != "P4":
                    ok = series_idx[rel_day[series_idx] <= p0]
                    binge_title = ok[np.argsort(rel_day[ok])[-40:]][rng.integers(0, min(40, len(ok)))]
                    binge_win = (p0, p1)
                if p1 > HORIZON:                       # censored – still active
                    break
                # ---- renewal decision at period end
                if plan == "P4":
                    h = ANNUAL_RENEWAL_CHURN
                else:
                    h = BASE_HAZARD.get(k, LATE_HAZARD)
                    if promo and attempt == 1:
                        h *= 2.2 if k == 2 else (1.3 if k in (1, 3) else 1.0)
                h *= (0.45 if k <= 6 else 0.7) if activated else 1.15
                h *= PLAN_HAZARD[plan] * CHANNEL_HAZARD[ch] * hz_user
                if n_unresolved >= 2:
                    h *= 2.5
                if last_unresolved is not None and p1 - last_unresolved <= 60:
                    h *= 1.5
                if attempt > 1:
                    h *= 0.85
                h = min(h, .9)
                if binge_win is not None and k == binge_period and rng.random() < .65:
                    h = 1.0
                if rng.random() < h:
                    status, cancel_type, end_day = "cancelled", "voluntary", p1
                    cancel_day = p1 - rng.uniform(0, 20)
                    break

            # ---- viewing sessions for this subscription window
            win_s = sday + 7 if attempt == 1 else sub_start
            win_e = min(end_day if end_day is not None else HORIZON + 1, HORIZON + 1)
            before = len(sink.sess_day)
            sessions_for_window(sink, uid, win_s, win_e, lam,
                                cancel_day, decays, dip, binge_win)
            if binge_win is not None and len(sink.sess_day) > before:
                sink.sess_title.append((len(sink.sess_day) - 1, ("BINGE", binge_title)))

            sink.subs.append((sub_id, uid, plan, sub_start, end_day, status, cancel_type))

            # ---- exit survey (voluntary only, 65% response)
            if cancel_type == "voluntary" and rng.random() < .65:
                if binge_win is not None and k == binge_period and rng.random() < .6:
                    reason = "finished_show"
                elif promo and attempt == 1 and k <= 3 and rng.random() < .5:
                    reason = "price"
                elif n_unresolved >= 2 and rng.random() < .5:
                    reason = "technical_issues"
                else:
                    reason = rng.choice(["price", "not_enough_content", "not_using_enough",
                                         "switching_service", "other"],
                                        p=[.25, .25, .30, .12, .08])
                if reason == "switching_service":
                    sw = rng.choice(COMPETITORS, p=[.33, .14, .15, .20, .13, .05])
                else:
                    sw = rng.choice(["None"] + COMPETITORS, p=[.45, .15, .08, .08, .10, .08, .06])
                sink.survey.append((uid, sub_id, cancel_day, reason, sw))

            # ---- win-back (pattern 7)
            if status == "active" or winback_done or rng.random() >= WINBACK_P:
                break
            gap = rng.uniform(20, 180)
            nxt = int(end_day + gap)
            if nxt > HORIZON:
                break
            winback_done, attempt, sub_start = True, 2, nxt
            plan = rng.choice(["P1", "P2", "P3"], p=[.40, .42, .18])
            binge_user = False

        sink.truth.append((uid, round(eng, 4), titles_first7, activated, binge_period is not None,
                           decays, dip is not None))
        if (i + 1) % 10_000 == 0:
            print(f"  simulated {i + 1:,} users")

    return sink, rel_day, pop_p


# ------------------------------------------------------------------ export
def to_date(day):
    return START + pd.to_timedelta(np.asarray(day, float), unit="D")


def build_tables(users, titles, sink, rel_day, pop_p):
    uid_str = users.user_id.to_numpy()
    plan_ids = PLANS.plan_id.to_numpy()

    subs = pd.DataFrame(sink.subs, columns=["sub_id", "uid", "plan_id", "s", "e", "status", "cancel_type"])
    subs = pd.DataFrame({
        "sub_id": subs.sub_id.map(lambda x: f"S{x:07d}"),
        "user_id": uid_str[subs.uid],
        "plan_id": subs.plan_id,
        "start_date": to_date(subs.s).normalize(),
        "end_date": to_date(subs.e.astype(float)).normalize(),
        "status": subs.status,
        "cancel_type": subs.cancel_type,
    }).sort_values("sub_id").reset_index(drop=True)

    pays = pd.DataFrame(sink.pays, columns=["payment_id", "sub", "d", "amount_thb", "status"])
    pays = pd.DataFrame({
        "payment_id": pays.payment_id.map(lambda x: f"PM{x:08d}"),
        "sub_id": pays["sub"].map(lambda x: f"S{x:07d}"),
        "payment_date": to_date(pays.d).normalize(),
        "amount_thb": pays.amount_thb,
        "status": pays.status,
    })

    tk = pd.DataFrame(sink.tickets, columns=["ticket_id", "uid", "d", "category", "resolved_hours"])
    tk = pd.DataFrame({
        "ticket_id": tk.ticket_id.map(lambda x: f"TK{x:07d}"),
        "user_id": uid_str[tk.uid],
        "created_at": to_date(tk.d).floor("min"),
        "category": tk.category,
        "resolved_hours": tk.resolved_hours,
    })

    sv = pd.DataFrame(sink.survey, columns=["uid", "sub", "d", "reason", "switched_to"])
    survey = pd.DataFrame({
        "user_id": uid_str[sv.uid],
        "sub_id": sv["sub"].map(lambda x: f"S{x:07d}"),
        "cancel_date": to_date(sv.d).normalize(),
        "reason": sv.reason,
        "switched_to": sv.switched_to,
    })

    # ---- sessions: assign titles
    lens = np.array([len(a) for a in sink.sess_day])
    offs = np.concatenate([[0], np.cumsum(lens)])
    s_user = np.concatenate(sink.sess_user)
    s_day = np.concatenate(sink.sess_day)
    s_binge = np.concatenate(sink.sess_binge)
    s_title = np.full(len(s_day), -1)
    for chunk_idx, spec in sink.sess_title:
        a, b = offs[chunk_idx], offs[chunk_idx + 1]
        if isinstance(spec, tuple) and spec[0] == "BINGE":
            m = s_binge[a:b]
            s_title[a:b][m] = spec[1]
        else:
            s_title[a:a + len(spec)] = spec
    todo = np.where(s_title < 0)[0]
    s_title[todo] = rng.choice(N_TITLES, len(todo), p=pop_p)
    for _ in range(30):                                # enforce release <= watch day
        bad = todo[rel_day[s_title[todo]] > s_day[todo]]
        if len(bad) == 0:
            break
        s_title[bad] = rng.choice(N_TITLES, len(bad), p=pop_p)
    ok = rel_day[s_title] <= s_day
    s_user, s_day, s_title, s_binge = s_user[ok], s_day[ok], s_title[ok], s_binge[ok]

    order = np.lexsort((s_day, s_user))
    s_user, s_day, s_title, s_binge = s_user[order], s_day[order], s_title[order], s_binge[order]
    is_series = (titles.content_type.to_numpy() == "series")[s_title]
    mins = np.where(is_series, rng.lognormal(3.6, .5, len(s_day)), rng.lognormal(4.6, .25, len(s_day)))
    mins = np.where(s_binge, mins * 1.6, mins).clip(1, 400).round().astype(int)
    pref = users.device.to_numpy()[s_user]
    dev = np.where(rng.random(len(s_day)) < .75, pref, rng.choice(DEVICES, len(s_day), p=DEVICE_P))
    sessions = pd.DataFrame({
        "session_id": [f"VS{i:08d}" for i in range(1, len(s_day) + 1)],
        "user_id": uid_str[s_user],
        "start_ts": to_date(s_day).floor("min"),
        "minutes": mins,
        "title_id": titles.title_id.to_numpy()[s_title],
        "device": dev,
    })

    truth = pd.DataFrame(sink.truth, columns=["uid", "engagement", "titles_first7_planned", "activated",
                                              "binge_user", "decay_before_cancel", "has_random_dip"])
    truth.insert(0, "user_id", uid_str[truth.uid])
    truth = truth.drop(columns="uid")

    users_out = users.drop(columns="_plan")
    return {"users": users_out, "plans": PLANS, "subscriptions": subs, "payments": pays,
            "viewing_sessions": sessions, "titles": titles, "support_tickets": tk,
            "exit_survey": survey}, truth


def add_shared_account_reason(tables: dict) -> None:
    """Pattern 9: some cancellers leave to join a cheaper shared account.

    Applied after the main simulation with its OWN random stream, so every
    other table stays identical. Price-sensitive customers (Basic plan, first
    subscription bought on promo, younger) are more likely to give this reason.
    Technical issues and finished-show answers are left untouched.
    """
    r = np.random.default_rng(SEED + 9)
    sv, subs, users = tables["exit_survey"], tables["subscriptions"], tables["users"]
    first = subs.sort_values("start_date").groupby("user_id").sub_id.first()
    d = sv.merge(subs[["sub_id", "plan_id"]], on="sub_id").merge(
        users[["user_id", "acquired_with_promo", "age_band"]], on="user_id")
    p = (0.07
         * np.where(d.plan_id == "P1", 2.0, np.where(d.plan_id == "P3", 0.6, 1.0))
         * np.where(d.acquired_with_promo & d.sub_id.isin(first.values), 1.6, 1.0)
         * d.age_band.map({"18-24": 1.8, "25-34": 1.3, "35-44": 1.0, "45-54": 0.6, "55+": 0.4}).to_numpy())
    eligible = ~d.reason.isin(["technical_issues", "finished_show"]).to_numpy()
    hit = eligible & (r.random(len(d)) < p)
    where = r.choice(["StreamTH shared account", "Netflix", "iQIYI/WeTV", "Other"],
                     len(d), p=[.45, .25, .20, .10])
    sv.loc[hit, "reason"] = "joined_shared_account"
    sv.loc[hit, "switched_to"] = where[hit]


def main():
    t0 = time.time()
    RAW.mkdir(parents=True, exist_ok=True)
    SIM.mkdir(parents=True, exist_ok=True)
    titles = make_titles()
    users = make_users()
    print(f"Simulating {N_USERS:,} users …")
    sink, rel_day, pop_p = simulate(users, titles)
    tables, truth = build_tables(users, titles, sink, rel_day, pop_p)
    add_shared_account_reason(tables)
    for name, df in tables.items():
        df.to_csv(RAW / f"{name}.csv", index=False)
        print(f"  {name:<18} {len(df):>10,} rows")
    truth.to_csv(SIM / "ground_truth_users.csv", index=False)
    print(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
