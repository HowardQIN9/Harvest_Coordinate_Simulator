# Joint downstream-capacity mechanism run

## Run identity

- Command: `python run_experiments.py --joint-capacity-mechanism`
- Date: 2026-09-13
- Random seed: 42
- Policies: Fixed, Static Threshold, Current-State Reactive, Finite-Horizon Predictive
- Field order: the same provisional far-to-near order for every policy
- Moisture realization: paired across all policies within every scenario
- Wet-bin capacities: 5,000, 10,000, and 20,000 bu
- Predictive horizon: 48 h
- Decision interval: 15 min
- STOP rule: eligible after 12 productive h; minimum duration 8 h

The earlier dryer-only mechanism results remain in `../mechanism/` and were not overwritten.

## Joint-capacity construction

The experiment multiplies the complete provisional dryer curve and grain-leg rate by the same capacity scale. Because semis unload directly through the grain leg, nominal unload time is divided by the same scale.

| Joint scale | Dryer at 18/20/22% (bu/h) | Grain leg (bu/h) | Nominal unload | Minimum field transport (bu/h) | Effective capacity at mean/worst moisture (bu/h) | Maximum harvest/effective ratio, mean/worst |
|---:|---:|---:|---:|---:|---:|---:|
| 1× | 800 / 600 / 400 | 3,000 | 20 min | 3,000 | 600 / 400 | 6.67 / 10.00 |
| 4× | 3,200 / 2,400 / 1,600 | 12,000 | 5 min | 4,000 | 2,400 / 1,600 | 1.67 / 2.50 |
| 10× | 8,000 / 6,000 / 4,000 | 30,000 | 2 min | 4,286 | 4,286 / 4,000 | 0.93 / 1.00 |

The 10× condition is the intended nonbinding/weak condition for the realized 18–22% moisture range. It is a mechanism scenario, not a claim about Aaron's actual infrastructure.

## Results

Harvest completion time is shown in hours. `Predictive benefit` is `Reactive - Predictive`, so a positive value favors Predictive. `Block benefit` is the corresponding reduction in forced blocking.

| Scale | Wet-bin scale | Fixed | Threshold | Reactive | Predictive | Predictive benefit | Block benefit |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1× | 0.5× | 193.64 | 202.69 | 196.48 | 197.55 | -1.07 | 23.04 |
| 1× | 1× | 186.16 | 190.60 | 189.55 | 188.17 | 1.38 | 26.13 |
| 1× | 2× | 169.70 | 170.14 | 172.60 | 174.26 | -1.66 | 31.87 |
| 4× | 0.5× | 49.12 | 80.06 | 49.12 | 53.60 | -4.48 | 8.90 |
| 4× | 1× | 47.47 | 51.27 | 47.47 | 54.76 | -7.29 | 8.51 |
| 4× | 2× | 43.09 | 48.60 | 43.09 | 54.71 | -11.63 | 4.26 |
| 10× | 0.5× | 38.35 | 44.29 | 38.35 | 38.35 | 0.00 | 0.00 |
| 10× | 1× | 38.35 | 44.29 | 38.35 | 38.35 | 0.00 | 0.00 |
| 10× | 2× | 38.35 | 44.29 | 38.35 | 38.35 | 0.00 | 0.00 |

## Preliminary interpretation

1. **The weak-regime boundary is now represented correctly.** At 10×, Fixed, Reactive, and Predictive are identical: 38.35 h harvest completion, zero forced blocking, and zero planned STOP. Prediction has no additional value when the end-to-end downstream system can accept the maximum production rate.
2. **The severe regime still shows no stable harvest-time benefit.** At 1×, Predictive reduces forced blocking by 23–32 h but replaces much of it with planned STOP. Its harvest-time effect relative to Reactive changes sign across buffer sizes.
3. **The current Predictive controller performs poorly in the 4× transition candidate.** It reduces forced blocking by 4–9 h but completes harvest 4.5–11.6 h later than Reactive. Larger buffer does not correct this behavior.
4. **The 4× result is a diagnostic, not evidence that prediction is harmful in general.** It indicates a likely controller-design issue involving the 48-h terminal condition, aggregate internal rollout, mode-selection timing, or the one-switch constraint. These mechanisms must be inspected before changing the controller.
5. **Static Threshold is not a reliable main baseline.** At 10× it slows harvest even though the end-to-end system is nonbinding. At 4× and the smallest wet bin it introduces a long planned STOP and reaches 80.06 h.

No parameter should be retuned merely to make Predictive win. The next analysis should trace the first action divergence between Reactive and Predictive in one 4× scenario, then determine whether the forecast objective, horizon treatment, or physical approximation caused the unnecessary slowdown.

## Saved artifacts

`joint_capacity_summary.csv` contains all 36 policy-scenario rows and derived end-to-end capacity ratios. `run_manifest.json` records the command and design factors. Every scenario directory contains:

- `scenario_config.json`
- `field_scenario.csv`
- `policy_comparison.csv`
- `machine_hours_summary.csv`
- `daily_machine_hours_comparison.csv`
- one directory per policy containing `metrics.json`, `timeseries.csv`, `transport_events.csv`, `daily_machine_hours.csv`, and `overview.svg`

Validation after the run found 36 metrics files, 36 complete time-series files, 36 transport-event files, 36 daily machine-hour files, 36 overview plots, nine scenario configurations, complete four-policy pairing, and zero final mass-balance error in every run.
