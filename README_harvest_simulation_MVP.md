# Harvest–Downstream Coordination Simulation MVP

> **Historical design document.** The equivalent-buffer downstream model and its
> buffer thresholds, output schema, and example rates below have been superseded.
> The current implementation uses a 500-bu combine tank, one 1000-bu semi,
> delivery at 20 minutes, empty return at 45 minutes, and continuous GC processing
> at 1000 bu/h with unlimited GC inventory. See [README.md](README.md) for the
> current model, assumptions, parameters, run commands, and completion metrics.

## 1. Goal

Build a minimal simulation to test whether **downstream-aware harvesting coordination** can reduce downstream-induced combine waiting when different field tasks naturally generate grain at different rates.

The MVP does **not** assume that `opening = slow mode` and `interior harvest = fast mode`.

Instead, each field contains two required operation phases:

1. `Opening`
2. `InteriorHarvest`

Their effective field capacities depend on field geometry.

The main hypothesis for the MVP is:

> When downstream processing capacity lies between the grain-generation rates of different available field tasks, changing the order of field tasks may reduce downstream congestion and total combine waiting, provided that the benefit exceeds the field-switching cost.

---

## 2. MVP Scope

### Included

- One combine initially
- Multiple fields
- Two field geometries:
  - approximately rectangular
  - irregular
- Two operation phases per field:
  - opening
  - interior harvest
- Grain generation from harvested acreage
- One equivalent downstream buffer
- Constant downstream processing capacity
- Field/task switching cost
- Sequential baseline policy
- Simple downstream-aware adaptive policy
- Time-step simulation

### Not included in MVP

Do **not** add these yet:

- grain cart routing
- semi-truck discrete-event transportation
- multiple combines
- real road network
- detailed GPS path planning
- grain dryer physics
- machine dynamics
- fuel model
- predictive ML model
- stochastic yield/moisture

These can be added only after the basic mechanism works.

---

# 3. Field Model

Each field has total area:

```text
50 acre
```

Split into:

```text
Opening area        = 10 acre
Interior area       = 40 acre
Total field area    = 50 acre
```

Therefore:

```text
A_open : A_interior = 10 : 40
```

Each field must satisfy the precedence constraint:

```text
Opening -> InteriorHarvest
```

The interior of a field cannot be harvested until that field's opening phase is completed.

---

# 4. Field Geometry Types

Use two geometry classes.

## 4.1 Approximately Rectangular Field

The opening operation is only moderately less efficient than normal interior harvesting.

Baseline capacities:

```text
C_interior       = 5.2 ac/h
C_open_rect      = 4.6 ac/h
```

Therefore:

```text
C_open_rect < C_interior
```

but the difference is relatively small.

## 4.2 Irregular Field

Opening an irregular field contains more maneuvering, turning, alignment, partial-width harvesting, etc.

For the first MVP, use an assumed value:

```text
C_open_irregular = 3.5 ac/h
```

Important:

```text
3.5 ac/h is currently an assumed simulation parameter,
not a measured value.
```

Later it should be calibrated using GPS-derived effective field capacity.

For simplicity in the first MVP, use the same interior harvesting capacity for both field geometries:

```text
C_interior = 5.2 ac/h
```

Thus the baseline ordering is:

```text
C_open_irregular < C_open_rect < C_interior

3.5 < 4.6 < 5.2 ac/h
```

---

# 5. Combine Parameter

Header width:

```text
30 ft
```

For the first MVP, header width is stored as a machine parameter but is **not used directly** to calculate effective capacity because effective field capacity is already given in `ac/h`.

Later versions may derive capacity from:

```text
speed x header width x field efficiency
```

---

# 6. Crop and Grain Generation

Use corn rather than wheat for the downstream simulation.

Baseline corn yield:

```text
Yield = 200 bu/ac
```

For any harvesting task:

```text
Q_harvest = C_task * Yield
```

where:

```text
Q_harvest = grain generation rate [bu/h]
C_task    = effective field capacity [ac/h]
Yield     = crop yield [bu/ac]
```

## Baseline Grain Generation Rates

### Interior harvesting

```text
5.2 ac/h * 200 bu/ac = 1040 bu/h
```

Therefore:

```text
Q_interior = 1040 bu/h
```

### Rectangular field opening

```text
4.6 ac/h * 200 bu/ac = 920 bu/h
```

Therefore:

```text
Q_open_rect = 920 bu/h
```

### Irregular field opening

```text
3.5 ac/h * 200 bu/ac = 700 bu/h
```

Therefore:

```text
Q_open_irregular = 700 bu/h
```

Summary:

| Task | Effective capacity | Grain generation |
|---|---:|---:|
| Irregular opening | 3.5 ac/h | 700 bu/h |
| Rectangular opening | 4.6 ac/h | 920 bu/h |
| Interior harvest | 5.2 ac/h | 1040 bu/h |

---

# 7. Downstream Model

For the MVP, represent all downstream logistics using one equivalent buffer plus one constant processing rate.

This is **not yet a literal truck model**.

It represents aggregate temporary downstream storage/transport availability.

## Buffer Capacity

Set the maximum buffer equal to approximately one standard grain semi load:

```text
B_max = 1000 bu
```

Interpretation:

```text
Equivalent downstream buffer capacity ~= one semi-trailer load
```

Do not model truck departure/return cycles yet.

---

# 8. Downstream Processing Rate

Baseline:

```text
D = 1000 bu/h
```

The reason for choosing this baseline is that it lies between the different grain-generation rates:

```text
Q_open_irregular < Q_open_rect < D < Q_interior

700 < 920 < 1000 < 1040 bu/h
```

Therefore:

### During interior harvesting

```text
1040 - 1000 = +40 bu/h
```

The buffer slowly fills.

### During rectangular opening

```text
920 - 1000 = -80 bu/h
```

The downstream buffer can recover.

### During irregular opening

```text
700 - 1000 = -300 bu/h
```

The downstream buffer recovers faster.

This creates the basic coordination mechanism that the MVP is intended to test.

---

# 9. Buffer Dynamics

Use a one-minute time step:

```text
dt = 1 min = 1/60 h
```

The ideal buffer equation is:

```text
B(t + dt) = B(t) + Q_harvest(t) * dt - D * dt
```

with physical bounds:

```text
0 <= B(t) <= B_max
```

Implementation should account for both empty-buffer and full-buffer boundaries.

A useful implementation concept is:

```text
incoming_grain  = Q_harvest * dt
processing_need = D * dt

available_grain = B + incoming_grain
processed_grain = min(available_grain, processing_need)
new_buffer      = available_grain - processed_grain
```

If `new_buffer > B_max`, the excess grain cannot enter the downstream system.

That excess should create a harvesting blockage / combine waiting event.

Do not simply clip the buffer to `B_max` and discard grain.

Mass must be conserved.

---

# 10. Combine Blocking Logic

The combine can harvest only if downstream capacity exists for the generated grain.

If the downstream buffer is full and the downstream processing rate is insufficient to accept all newly produced grain, the combine becomes partially or fully blocked.

Track:

```text
blocked_time
blocked_grain
productive_harvest_time
```

For the first implementation, a simple discrete approximation is acceptable:

```text
If the full one-minute harvest production cannot fit,
reduce productive harvest during that time step to the amount downstream can accept.
Count the remaining fraction of the minute as blocked time.
```

A more exact event-based treatment can be added later.

---

# 11. Switching Cost

Whenever the combine switches from its current field to a different field:

```text
T_switch = 20 min
```

or:

```text
T_switch = 0.3333 h
```

During switching:

```text
Q_harvest = 0
```

but downstream processing continues.

Therefore, during a complete 20-minute switch with the baseline downstream rate:

```text
1000 bu/h * 1/3 h ~= 333 bu
```

may be removed from the buffer if sufficient grain is available.

Important:

Switching is a real cost and must be included in total completion time.

The adaptive controller must not be rewarded simply for switching frequently and letting the downstream system drain.

Track:

```text
number_of_switches
switching_time
```

---

# 12. Initial Field Set

Start with six 50-acre fields.

Example:

```text
F1: rectangular
F2: rectangular
F3: irregular
F4: rectangular
F5: irregular
F6: irregular
```

Each field contains:

```text
Opening         = 10 acre
InteriorHarvest = 40 acre
```

Initial state of every field:

```text
opening_remaining  = 10 acre
interior_remaining = 40 acre
opening_complete   = false
field_complete     = false
```

---

# 13. Task Availability

For each field:

### Opening task is available if

```text
opening_remaining > 0
```

### Interior task is available if

```text
opening_complete == true
AND
interior_remaining > 0
```

A field is complete when:

```text
opening_remaining == 0
AND
interior_remaining == 0
```

---

# 14. Baseline Strategy: Sequential Harvesting

The first simulation should contain **no adaptive control**.

Harvest fields sequentially:

```text
Open F1
-> Interior F1
-> switch
-> Open F2
-> Interior F2
-> switch
-> Open F3
-> Interior F3
-> ...
```

In short:

```text
O1 -> I1 -> O2 -> I2 -> O3 -> I3 -> ...
```

Apply the 20-minute switching cost only when moving from one field to another.

Do not apply a field-switch cost when moving from `Opening` to `InteriorHarvest` within the same field unless a later model explicitly introduces an intra-field setup cost.

The purpose of this first run is only to validate:

- acreage accounting
- grain mass balance
- buffer dynamics
- blocking logic
- task completion
- switching cost

---

# 15. Adaptive Strategy

Only implement this after the sequential model is working correctly.

The adaptive controller chooses the next field using downstream buffer state,
only when no field has been started or the current field is complete. Once a
field is started, its opening and interior harvest must both finish before any
field switch. The task preferences below apply only at those decision points;
they cannot interrupt an unfinished field.

The controller should exploit **naturally available tasks with different grain-generation rates** rather than inventing arbitrary fast/slow combine modes.

Possible first rule-based controller:

```text
High buffer threshold = 0.80 * B_max
Low buffer threshold  = 0.40 * B_max
```

Therefore:

```text
High threshold = 800 bu
Low threshold  = 400 bu
```

## Rule

When:

```text
B >= 800 bu
```

prefer an available lower-grain-rate task, especially:

```text
Irregular Opening
then Rectangular Opening
then Interior Harvest
```

When:

```text
B <= 400 bu
```

prefer a high-throughput task:

```text
Interior Harvest
```

Between 400 and 800 bu, retain the current task when possible to avoid unnecessary switching.

This hysteresis is intended to prevent:

```text
F -> S -> F -> S -> F -> S
```

style oscillation.

---

# 16. Switching Decision Constraint

Both policies must complete the current field before switching to another field:

```text
Field switch permitted only if:
opening_remaining == 0 AND interior_remaining == 0
```

This is a hard constraint enforced by the simulator, including during downstream
blocking. Buffer thresholds and minimum task duration do not override it.
Transitioning from opening to interior harvest in the same field costs no switch
time. Moving to another field costs 20 minutes while downstream processing continues.

Adaptive control can change field order, but cannot interleave phases from
different fields. Six initially unharvested fields therefore require five field
switches under either policy.

---

# 17. Simulation State

Recommended global state variables:

```text
time_h
buffer_bu
current_field
current_task
combine_state
switch_time_remaining
```

Possible combine states:

```text
HARVESTING
SWITCHING
BLOCKED
DONE
```

For every field:

```text
field_id
geometry
opening_remaining_ac
interior_remaining_ac
opening_complete
field_complete
```

---

# 18. Simulation Loop

At each one-minute step:

```text
1. Check whether simulation is complete.

2. If combine is switching:
      - Q_harvest = 0
      - reduce switch_time_remaining
      - downstream continues processing buffer

3. Else determine current feasible task.

4. Obtain task effective capacity C_task.

5. Compute potential harvested acreage:
      dA = C_task * dt

6. Limit dA by remaining acreage in the current task.

7. Compute potential grain generation:
      dG = dA * Yield

8. Process downstream grain at rate D.

9. Determine how much of dG can enter the downstream system
   without exceeding B_max.

10. If all dG can enter:
       - harvest full dA
       - update field remaining area
       - update buffer

11. If only part of dG can enter:
       - harvest only the feasible fraction
       - record blocked fraction of the time step
       - conserve grain mass

12. If the current task finishes:
       - update field/task state
       - choose next task according to controller
       - if next task is in another field, start 20-min switch

13. Record time-series outputs.

14. Advance time by dt.
```

---

# 19. Core Outputs

The first MVP should record at least:

```text
Total harvest completion time [h]
Total productive harvesting time [h]
Total downstream-induced blocked time [h]
Total switching time [h]
Number of field switches
Combine utilization
Total harvested acreage [ac]
Total harvested grain [bu]
Total processed grain [bu]
Final downstream buffer [bu]
Maximum buffer level [bu]
Average buffer level [bu]
```

Define combine productive utilization as:

```text
combine_utilization = productive_harvest_time / total_completion_time
```

Also record a time series containing:

```text
time
buffer_level
current_field
current_task
combine_state
cumulative_harvested_acres
cumulative_grain_generated
cumulative_grain_processed
```

---

# 20. Required Plots

After the sequential baseline works, generate at least:

## Plot 1 — Buffer level vs. time

```text
x = time
Y = buffer_bu
```

Show horizontal line at:

```text
B_max = 1000 bu
```

## Plot 2 — Combine task/state vs. time

Show when the combine is:

```text
Opening
InteriorHarvest
Switching
Blocked
```

## Plot 3 — Cumulative harvested acreage vs. time

## Plot 4 — Cumulative downstream processed grain vs. time

---

# 21. Downstream Capacity Sensitivity Analysis

After baseline `D = 1000 bu/h` is working, run:

```text
D = [600, 800, 900, 1000, 1100, 1200] bu/h
```

This creates different bottleneck regimes.

## Severe bottleneck

Example:

```text
D = 600 bu/h
```

Then:

```text
700 > 600
920 > 600
1040 > 600
```

All harvesting tasks generate grain faster than downstream can process it.

Expected result:

```text
Coordination has limited ability to eliminate congestion.
```

## Moderate bottleneck

Example:

```text
D = 800 bu/h
```

Then:

```text
700 < 800 < 920 < 1040
```

Only irregular opening naturally drains the buffer.

## Critical / interesting region

Baseline:

```text
D = 1000 bu/h
```

Then:

```text
700 < 920 < 1000 < 1040
```

Opening tasks can drain the buffer while interior harvesting slowly fills it.

This is expected to provide the clearest test of downstream-aware coordination.

## No meaningful bottleneck

Example:

```text
D = 1200 bu/h
```

Then:

```text
700 < 920 < 1040 < 1200
```

All tasks generate grain slower than downstream can process it.

Expected result:

```text
Adaptive ~= Sequential
```

because downstream-aware scheduling is unnecessary.

---

# 22. Important Sanity Checks

The simulator must satisfy these checks before adding complexity.

## Check 1 — Total acreage

Six fields x 50 acre:

```text
Total area = 300 acre
```

At the end:

```text
cumulative_harvested_acres = 300 acre
```

## Check 2 — Total grain

At 200 bu/ac:

```text
300 acre * 200 bu/ac = 60,000 bu
```

At the end:

```text
Total grain harvested = 60,000 bu
```

## Check 3 — Mass balance

At every time:

```text
Cumulative grain harvested
=
Cumulative grain processed
+ current buffer grain
```

assuming no grain loss.

Numerical tolerance is acceptable.

## Check 4 — Buffer bounds

Always:

```text
0 <= B <= 1000 bu
```

## Check 5 — No bottleneck scenario

At sufficiently high D, blocked time should approach zero.

## Check 6 — Very low downstream capacity

At sufficiently low D, blocked time should become large.

## Check 7 — No artificial benefit from switching

An adaptive strategy that switches more often must pay the full 20-minute switching cost.

Total completion time must include:

```text
productive harvesting
+ switching
+ downstream-induced blocking
```

---

# 23. Main Experimental Comparison

After validating the simulator, compare:

```text
Policy 1: Sequential
Policy 2: Downstream-aware adaptive
```

Primary metrics:

```text
Total completion time
Downstream-induced blocked time
Switching time
Number of switches
Combine utilization
```

The adaptive policy is useful only if reduced downstream waiting is large enough to offset additional switching cost.

---

# 24. Core Mechanism to Test

The MVP is based on the condition that different feasible field tasks can naturally produce different grain inflow rates.

For a useful coordination opportunity, a particularly interesting case is:

```text
Q_low < D < Q_high
```

For the baseline parameters:

```text
Q_open_irregular = 700 bu/h
Q_open_rect      = 920 bu/h
D                = 1000 bu/h
Q_interior       = 1040 bu/h
```

Therefore:

```text
700 < 920 < 1000 < 1040
```

The hypothesis is **not**:

```text
Slow harvesting is better.
```

The hypothesis is:

> When downstream capacity is temporarily constrained, the controller may use already-required lower-inflow field tasks to reduce downstream-induced combine waiting, if doing so provides enough benefit to compensate for switching costs.

---

# 25. Interpretation of Rectangular vs. Irregular Fields

Do not hard-code the conceptual assumption:

```text
Opening = slow
Interior = fast
```

Instead use:

```text
Field geometry + operation phase -> effective field capacity
```

Current MVP:

```text
Rectangular opening:   4.6 ac/h
Irregular opening:     3.5 ac/h   [assumed]
Interior harvesting:   5.2 ac/h
```

Later, these capacities should come from measured GPS/header trajectories and field geometry.

If future data show that rectangular opening is approximately equal to, or occasionally faster than, interior harvesting, the simulator should allow that without changing its structure.

---

# 26. Parameter Classification

## Parameters based on current working values / measurements

```text
C_interior     = 5.2 ac/h
C_open_rect    = 4.6 ac/h
Header width   = 30 ft
Opening area   = 10 acre
Interior area  = 40 acre
Switching time = 20 min
```

## Baseline modeling choices for MVP

```text
Corn yield          = 200 bu/ac
B_max               = 1000 bu
D                   = 1000 bu/h
dt                  = 1 min
Number of fields    = 6
Field geometries    = 3 rectangular + 3 irregular
Adaptive thresholds = 800 / 400 bu
```

## Explicitly assumed parameter requiring later calibration

```text
C_open_irregular = 3.5 ac/h
```

---

# 27. Recommended Implementation Order

Do not implement everything at once.

## Stage 1 — Single field, no buffer constraint

Verify:

```text
Opening -> InteriorHarvest
```

and acreage completion times.

## Stage 2 — Single field + downstream buffer

Verify:

```text
acre -> bushel conversion
buffer mass balance
processing
blocking
```

## Stage 3 — Six fields + sequential strategy

Add:

```text
field switching
20-minute switch cost
```

Validate all sanity checks.

## Stage 4 — Adaptive rule-based controller

Add buffer thresholds and task selection.

Compare against Sequential.

## Stage 5 — Sensitivity analysis

Sweep downstream processing capacity:

```text
600 to 1200 bu/h
```

Then optionally sweep switching cost.

Only after these stages work should the model be expanded to multiple combines, grain carts, semi trucks, prediction, or real field geometry.

---

# 28. Suggested Code Structure

A simple structure is sufficient:

```text
harvest_sim/
|
|-- README.md
|-- config.py
|-- field.py
|-- combine.py
|-- downstream.py
|-- controller.py
|-- simulator.py
|-- run_baseline.py
|-- run_experiments.py
|-- plotting.py
`-- results/
```

Possible responsibilities:

## `config.py`

Store all baseline parameters.

## `field.py`

Field geometry, remaining opening/interior acreage, task availability.

## `combine.py`

Combine state and current task.

## `downstream.py`

Buffer and processing dynamics.

## `controller.py`

Implement:

```text
SequentialController
AdaptiveController
```

## `simulator.py`

Main time-step loop and mass balance.

## `run_baseline.py`

Run one sequential baseline and plot/print diagnostics.

## `run_experiments.py`

Run parameter sweeps and policy comparisons.

---

# 29. First Success Criterion

The first successful simulation does **not** need to prove that the adaptive strategy is better.

The first success criterion is simply:

1. all 300 acres are harvested;
2. all 60,000 bu are conserved;
3. buffer never exceeds 1000 bu;
4. downstream capacity can create combine blocking;
5. switching consumes exactly 20 minutes when changing fields;
6. changing `D` produces logically different bottleneck regimes.

Only after these are correct should policy performance be interpreted.

---

# 30. Research Question Represented by This MVP

A concise version of the simulation question is:

> Can a downstream-aware harvest controller exploit naturally occurring differences in grain-generation rate across field geometry and operation phases to reduce downstream-induced combine waiting, while accounting for the cost of switching between fields?

The important explanatory variables are expected to be:

```text
Task-rate heterogeneity
Downstream bottleneck severity
Switching cost
```

Conceptually:

```text
Adaptive benefit
=
f(task-rate heterogeneity,
  downstream bottleneck severity,
  switching cost)
```

This is the mechanism the MVP should test before adding prediction or multi-machine fleet coordination.
