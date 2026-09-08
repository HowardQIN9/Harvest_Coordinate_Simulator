# Update notes — 2026-09-08

This update consolidates the current 10-field harvest scenario and adds a third
control policy for comparison. The main changes are summarized below.

## Scenario and dryer model

- The default scenario contains 10 fields totaling 650 ac. The final four
  fields use 15 min one-way travel; two are rectangular and two are irregular.
- Field moisture is sampled once at initialization from `U(18%, 22%)` with a
  fixed seed (`42` by default). All policies share the same realization, and a
  field's moisture remains fixed throughout the simulation.
- Every grain lot inherits its source field's moisture. The receiving system
  preserves `(quantity, moisture, field_id)` lots in FIFO order.
- Dryer throughput follows `D(M) = 2600 - 100M` bu/h, giving 800 bu/h at 18%
  and 400 bu/h at 22%.

## Downstream receiving and unloading

- Semi unloading is now a continuous flow rather than a fixed-delay atomic
  transfer. A full 1,000 bu load has a nominal unloading rate of 6,000 bu/h and
  therefore takes at least 10 min.
- Semi unloading, grain-leg transfer, and drying occur concurrently.
- A semi may start unloading without the pit first having room for the entire
  load. If the pit is full, unloading is throttled to the grain leg's actual
  outflow or paused when no outflow is possible.
- A semi returns to the field only after its load reaches zero. Pit and wet-bin
  capacity limits and mass conservation remain enforced.

## Adaptive policy

- The minimum Interior prediction horizon was increased from 2 h to 12 h.
- Before each scheduled 10 h harvest rest, Adaptive treats the FIFO-aware grain
  quantity needed to keep the dryer running as a minimum coverage requirement.
  Only predicted shortage is penalized; inventory above the requirement is not
  treated as an error.
- Transported grain is discounted by a configurable conservative fraction
  (`0.65` by default) when estimating how much will reach the wet bin before
  the rest period.
- Rest-start inventory, FIFO-aware target, target shortage/excess, rest-end
  inventory, and dryer utilization during complete rest windows are reported.

Important interpretation: the current controller is recalculated on a rolling
basis, but it is a lightweight single-field analytical forecast. It does not
yet clone the event state and simulate multiple fields over a complete future
12 h trajectory.

## Reactive policy

- Added `reactive` alongside `sequential` and `adaptive` in the simulator and
  command-line tools.
- Reactive keeps the Sequential field order and does not use a future forecast
  or the night-inventory target. This isolates immediate feedback control from
  predictive field selection.
- Its current pressure signal is the maximum of field-side mobile-storage
  pressure, total system inventory occupancy, and wet-bin occupancy.
- Interior mode thresholds are:
  - pressure below 40%: `2C+1GC`;
  - pressure from 40% to below 80%: `2C`;
  - pressure at or above 80%: `1C`.
- Reactive uses the same 30 min minimum dwell and maximum two mode switches per
  field as Adaptive.

## Current default-scenario results

| Metric | Sequential | Reactive | Adaptive |
| --- | ---: | ---: | ---: |
| Harvest completion (h) | 196.033 | 196.968 | 195.297 |
| System completion (h) | 205.733 | 205.730 | 205.730 |
| Forced blocking (h) | 84.493 | 68.161 | 56.413 |
| Intentional slowdown (h) | 0.000 | 38.967 | 48.144 |
| Interior mode switches | 0 | 20 | 19 |
| Rest-window dryer utilization | 100% | 100% | 100% |

The dryer remains the dominant bottleneck in this scenario. Reactive and
Adaptive reduce forced field blocking, but neither materially reduces total
system completion time once the dryer remains continuously supplied.

## Validation and known limitations

- The automated suite now contains 19 tests covering all three policies,
  continuous unloading/backpressure, FIFO moisture processing, scheduled rest,
  mode constraints, capacity bounds, and time-step mass conservation.
- The checked default runs finish with zero mass-balance error.
- Current formal results use one random seed. Multi-seed experiments and
  confidence intervals are still required for general conclusions.
- Field area and geometry remain confounded in the current table (50 ac fields
  are irregular and 80 ac fields are rectangular).
- Reactive reaches the two-switch limit in every default field, so its 40%/80%
  thresholds and switch constraint require a later sensitivity study.
- A true multi-field rolling-horizon event rollout remains future work.
