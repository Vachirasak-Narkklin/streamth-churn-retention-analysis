# Simulation Assumptions

> **All data in this project is synthetic.** StreamTH is a fictional Thai SVOD platform. No real company, customer or viewing data is used. Competitor names appear only as exit-survey answer categories.

## How the data is generated
`src/generate_data.py` (seed 42) runs a discrete-time renewal model for 50,000 subscribers who sign up between 2024-01 and 2026-10. Observation cut-off is **2026-10-31**; subscriptions still running then are right-censored (`status = 'active'`).

At the end of every billing period (30 days monthly, 365 days annual) each subscriber either:
1. renews,
2. cancels (**voluntary** churn), or
3. fails payment at the next renewal and is not recovered (**involuntary** churn).

Viewing sessions are an inhomogeneous Poisson process whose intensity depends on a hidden per-user engagement level, plus the behavioural effects below.

## Planted patterns
These effects were deliberately built into the simulation. The analysis phases must *recover* them from the raw tables using sound methods; `tests/test_data_quality.py` verifies each one is recoverable.

| # | Pattern | How it is simulated | Observed in raw data (seed 42) |
|---|---|---|---|
| 1 | Churn hazard peaks at Month 3, lower after Month 6 | Base tenure hazard curve with peak at period 3 | Monthly-plan hazard: M1 12.6%, M2 15.2%, **M3 18.4%**, M4 9.1%, M7+ ≈5% |
| 2 | Promo-acquired users churn ~2× at Month 2 | Hazard × 2.2 at period 2 (× 1.3 at periods 1 and 3); 50% off first 2 months | Promo / non-promo hazard at M2 = **1.83×** |
| 3 | Activation (≥3 titles in first 7 days) improves retention | Engagement drives early exploration; activated users hazard × 0.45 (periods 1–6) | 180-day retention: **68.4%** activated vs **49.3%** not activated |
| 4 | Usage decays before cancellation | 75% of voluntary churners: session intensity falls linearly over the 28 days before the cancel request | Sessions in last 14 days < 80% of prior 14 days (tested) |
| 5 | ≥2 unresolved support tickets raise churn risk | Hazard × 2.5 with ≥2 unresolved tickets; × 1.5 if an unresolved ticket in last 60 days | Period hazard 2.0–2.5× higher **within each tenure band** |
| 6 | Binge-and-leave | 12% of users binge one new series in period 1–3, then cancel at period end with 65% probability | Early churners ≥2× more likely to have one title ≥50% of recent sessions |
| 7 | ~15% of churners win back within 6 months | 15% of churners resubscribe 20–180 days later (once) | Tested in 10–20% band |
| 8 | Annual plans churn far less; involuntary ≈20% of churn | Annual renewal churn 22%; payment failure 2.2–3.6% per renewal, 35% recovered | Involuntary share **18.7%** of ended subscriptions |

## Deliberate noise (so patterns are not perfectly clean)
- Per-user random hazard multiplier (lognormal, σ = 0.3)
- 25% of voluntary churners show **no** usage decay before cancelling (silent churners)
- 20% of users have a random 30-day usage dip without churning (false-positive source for triggers)
- Payment failures are partly recovered by retries
- Exit survey response rate is 65%, and reasons are noisy

## Known analytical traps (intentional)
- **Tenure confounding (Simpson's paradox):** long-tenure users accumulate more support tickets *and* churn less, so a pooled comparison understates the ticket effect. Compare within tenure bands or use survival models.
- **Censoring:** recent cohorts have not had time to churn. Naïve "churn rate" by cohort is biased; use survival methods.
- **Silent churners:** a usage-based trigger cannot catch everyone; report recall honestly.

## Ground truth
Hidden simulation variables (engagement, activation flag, binge flag) are written to `data/simulation/ground_truth_users.csv` **for validation only**. They must never be used as features in analysis or modelling.
