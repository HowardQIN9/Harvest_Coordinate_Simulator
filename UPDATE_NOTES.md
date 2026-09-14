# Update notes — 2026-09-13

## Planned STOP and future grain-bag action

- Recorded that Planned STOP is a valid predictive action and is not an
  automatic loss when it improves completion time or an explicitly justified
  operating objective.
- Clarified that Predictive should compare high-rate harvesting, intentional
  slowdown, and legal Planned STOP by their final consequences rather than
  treating continuous dryer-rate matching as the controller goal.
- Identified a possible 48-hour terminal effect: an immediate 8-hour STOP may
  look inferior inside the horizon even if draining the system enables faster
  post-horizon completion.
- Preserved the current 4x result as evidence of anticipatory congestion
  management, not evidence that intentional slowdown is the optimal response.
- Defined the fairness requirement for a future grain-bag extension: Reactive
  and Predictive must share the same bag action, capacity, rates, costs, and use
  limits.

This revision replaces the earlier “design rules first” comparison with an experiment centered on the frozen research question:

> **When does predictive coordination provide additional value over current-state reactive coordination under downstream constraints?**

## Experimental logic

- Four formal policies now exist: `fixed`, `threshold`, `reactive`, and `predictive`.
- Reactive versus Predictive is the main comparison. Fixed and Static Threshold are references.
- Every policy uses the same field order and physical constraints. Predictive no longer gains an advantage by reordering fields.
- The current far-to-near order is explicitly provisional while the actual planting sequence is requested from Aaron.
- The experiment-design document now separates farmer-confirmed facts, supplied baseline values, exploratory assumptions, and open literature/interview questions.

## Production actions and STOP

- All effective harvest rates were reduced to 80% of the earlier nominal values: 1C = 1,600, 2C = 3,200, and 2C + 1GC = 4,000 bu/h.
- Opening is 15% of each field and may use 1C or 2C. Earlier rectangular/irregular efficiency ratios are retained.
- At most one interior production-mode switch is allowed per field.
- A 30-min dwell and 5-min switch penalty are explicit exploratory assumptions.
- The fixed 14-h-on/10-h-off schedule was removed.
- STOP is now a system scheduling action: it is eligible after 12 cumulative productive harvest hours and lasts at least 8 h.
- STOP halts new grain production only; field storage, semis, grain leg, wet bin, and dryer continue operating.
- Planned STOP and forced blocking are reported separately.

## Downstream model

- Combine capacity is 300 bu per active combine.
- One aggregate 1,000-bu grain cart is represented.
- The default fleet is three 1,000-bu semis.
- The receiving pit was removed. A semi unloads directly through one 3,000-bu/h grain leg, so a full load requires at least 20 min.
- The wet bin remains a finite 10,000-bu FIFO buffer.
- The dryer remains continuous and moisture-dependent. `D(M) = 2600 - 100M` over 18–22% is retained only as a provisional curve pending equipment/literature verification.

## Controllers

- `threshold` retains the old 40%/80% rate bands and a 95% STOP threshold as a conventional heuristic reference.
- `reactive` now derives its trigger from current field/system free storage, committed grain, current harvest/transport/dryer rates, and response time. It selects the fastest action with nonnegative physical headroom.
- `predictive` performs a transparent 48-h rolling-horizon rollout. It uses known future field volumes, moisture/FIFO dryer evolution, field-specific transport throughput, and transition constraints.
- All state-dependent policies reconsider actions every 15 min. The event simulation still advances at one-minute and event boundaries.
- The predictive score is lexicographic: remaining target grain, projected forced blocking, dryer idle time, and terminal inventory. Progress comes first so an indefinite STOP cannot appear optimal merely because it has zero forced blocking. No arbitrary weighted “benefit” score is used.
- Compatibility aliases remain: `sequential -> fixed`, `adaptive -> predictive`.

## Preliminary experiment

- `run_experiments.py --mechanism` runs a small 3 × 3 dryer-capacity/wet-bin map.
- Dryer scales are 1×, 4×, and 8×; wet-bin scales are 0.5×, 1×, and 2×.
- A focused 8/12/16-h work-before-STOP check is added at the middle capacity/buffer condition.
- The summary includes `Q_harvest / mean dryer rate` so the weak, transition, and severe regimes can be identified directly.
- This is an 11-scenario exploratory set, not a confirmatory full sweep.

## Validation

- Tests were rewritten around the new study definitions.
- Coverage includes direct 20-min unloading without a pit, wet-bin backpressure, FIFO drying, revised rates and opening area, shared field order, current-state information isolation, future-dependent Predictive behavior, one-switch enforcement, STOP eligibility/minimum duration, machine-hour accounting, capacity limits, and time-step mass conservation.
- Old generated result directories are not evidence for the revised model and must not be compared with new outputs.

Earlier model versions—including field reordering, 14-h/10-h fixed rest, one semi, a receiving pit, 5,000-bu/h sustained production, and a 12-h single-field forecast—are historical and superseded by this revision.

## Joint-capacity experiment record

The `--joint-capacity-mechanism` runner preserves the dryer-only results and
creates a separate 1×/4×/10× joint dryer, grain-leg, and nominal unload-service
map. It stores full time series and transport events for all 36 runs, plus
scenario configurations, summaries, plots, and a run manifest. In the 10× true
weak condition, Fixed, Reactive, and Predictive converge exactly. The 4× case
reveals a Predictive slowdown that requires diagnosis rather than parameter
retuning. See `results/current/joint_capacity_mechanism/RUN_RECORD.md`.
