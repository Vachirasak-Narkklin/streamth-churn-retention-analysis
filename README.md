# StreamTH — Churn & Retention Cohort Analysis

> 🚧 Work in progress — analysis is being added step by step.

Reducing subscription revenue loss for **StreamTH**, a fictional Thai streaming platform.
**All data is synthetic** — see [Simulation Assumptions](docs/simulation_assumptions.md).

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/generate_data.py      # ~20 s, writes data/raw/*.csv (seed 42)
pytest -q                        # data-quality + pattern-recovery tests
```

## Docs
- [Data dictionary](docs/data_dictionary.md)
- [Simulation assumptions](docs/simulation_assumptions.md)
- [Competitor pain points from Thai app reviews](docs/competitive_painpoints.md)

## Roadmap
- [x] Synthetic subscription dataset + data-quality tests
- [x] Competitor pain points from Thai app reviews
- [ ] SQL cohort retention
- [ ] Survival analysis
- [ ] Behavioural drivers
- [ ] Early warning system
- [ ] Intervention plan & ROI
- [ ] Leaky-bucket sizing
- [ ] Streamlit dashboard
- [ ] Executive summary
