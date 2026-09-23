# Autonomous Harvest Coordination: Frozen Exploratory Experiment Design

> [!IMPORTANT]
> **Historical design record.** This document preserves an earlier experiment
> definition and is not the specification for the current controller or
> validation suite. In particular, references to 12 h/8 h STOP rules, 15 min
> control, or a 48 h Beam/rollout design are superseded. Use
> [`README.md`](README.md) and the current code for the active methodology.

## 1. Frozen research question

> **When does predictive coordination provide additional value over current-state reactive coordination under downstream constraints?**

This wording is frozen for the present study. The central comparison is **Reactive versus Predictive**. Fixed and static-threshold policies are references that help interpret the result; they are not the main scientific comparison.

### Motivation and logic chain

1. Harvesting is a coupled production-logistics-processing system involving combines, a grain cart, semis, a grain leg, finite wet storage, and a dryer.
2. A locally reasonable field action can create a downstream bottleneck after a delay.
3. An experienced operator already forms an informal mental model of those delayed consequences.
4. An autonomous or decision-support controller can formalize this anticipation by projecting the consequence of each currently feasible action.
5. The scientific question is not whether prediction always wins. It is whether prediction adds value beyond a well-defined current-state controller, and in which constraint regime that value appears.

The paper is therefore an **operating-regime and mechanism study**, not simply a competition between four algorithms.

## 2. Evidence status: what is known and what remains provisional

The model must not present design choices as farmer-confirmed facts.

| Item | Current value or interpretation | Status | Required follow-up |
|---|---|---|---|
| Main harvest-order consideration | Earlier planting generally implies earlier harvest | Confirmed by Aaron | Ask for the actual corn planting sequence for the modeled fields |
| Within-field commitment | Normally finish a started field before moving | Confirmed by Aaron | Retain as a strong constraint |
| Distance effect | Important for reliability and hauling, but not the only order rule | Confirmed by Aaron | Do not claim that far-to-near is observed practice |
| Default field order | Far-to-near, shared by all policies | Provisional scenario assumption | Replace with actual planting order; later test near-to-far as a direction check |
| Combine grain tank | 270–300 bu per combine; model uses 300 bu | Confirmed by Aaron | None for the exploratory model |
| Peak 2C + 1GC rate | Approximately 5,000 bu/h in good conditions | Confirmed as a peak by Aaron | Do not use as sustained effective capacity |
| Effective harvest rates | 1C = 1,600; 2C = 3,200; 2C + 1GC = 4,000 bu/h | Modeling calibration: earlier nominal rates × 80% | Sensitivity or field-data calibration later |
| Opening area | 15% of each field | Exploratory assumption | Literature, field geometry calculation, or sensitivity |
| Opening action | Select 1C or 2C | Study design | Keep the geometry efficiency ratios from the earlier model |
| Interior mode changes | At most one change per field | Study design agreed for version 1 | Verify operational cost and feasibility |
| Mode-change penalty | 5 min after a minimum 30-min dwell | Exploratory assumption | Literature, observation, or sensitivity |
| STOP eligibility | At least 12 cumulative productive harvest hours since the previous STOP | Study design agreed for version 1 | Sensitivity at 8/12/16 h |
| Minimum STOP | At least 8 h | Study design agreed for version 1 | Sensitivity if it drives conclusions |
| Meaning of STOP | Grain production stops; material handling, semis, grain leg, wet bin, and dryer continue | Consistent with Aaron's description | Retain |
| Planned versus forced idle | Planned STOP can release labor to other work; forced blocking is an operating loss | Confirmed conceptually by Aaron | Do not combine the two into one idle-time metric |
| Mobile overnight buffer | Semis and grain cart may remain loaded while the dryer continues | Confirmed by Aaron | Retain |
| Grain cart | One aggregate 1,000-bu unit | Provisional parameter and boundary choice | Confirm capacity; detailed routing is out of scope |
| Semis | Three 1,000-bu semis | Current baseline supplied by the project | Record the original source and test fleet size later |
| Receiving pit | None | Confirmed by Aaron | Semi unloads directly through the grain leg |
| Grain leg | 3,000 bu/h; 1,000 bu unload takes about 20 min | Confirmed by Aaron | Retain one unload position and queue |
| Wet bin | 10,000 bu | Current baseline supplied by the project | Record source/measurement |
| Dryer observations | About 800 bu/h at 18%, 500 at 21%, and 200 at 30% moisture | Confirmed by Aaron as representative values | Verify equipment setup and operating curve |
| Version-1 dryer curve | Continuous FIFO, `D(M) = 2600 - 100M` bu/h over 18–22% | Provisional representation | Check manual and literature before confirmatory experiments |
| Dryer control | Dryer throughput is not an action in version 1 | Boundary decision | Ask whether setpoints are changed during a day and how quickly |
| Soybean and weather | Not modeled as crops or stochastic processes in version 1 | Explicit exclusion | Later represent as planned/forced availability interruptions if needed |

## 3. Model boundary

| Component | Why it is needed | Version-1 representation | Why this simplification is acceptable now |
|---|---|---|---|
| Field and field phase | Production source and geometry-dependent work | Every field has a 15% Opening followed by InteriorHarvest | Separates the slower opening operation without modeling paths or rows |
| Combine modes | Main controllable production rate | Opening: 1C or 2C; interior: 1C, 2C, or 2C + 1GC | Captures rate-control authority relevant to the RQ |
| Field sequence | Prevents field reordering from becoming an extra Predictive privilege | One fixed, shared sequence; finish each field before the next | Isolates anticipation from routing and scheduling authority |
| Grain cart | Provides field-side buffering and enables the highest interior rate | One aggregate 1,000-bu buffer | Cart routing is not the research question |
| Semis | Create finite mobile storage and distance-dependent transport delay | Three discrete 1,000-bu vehicles with travel, queue, unload, and return states | Vehicle availability is central to downstream coupling |
| Farm receiving | Converts returning loads into wet-bin inflow | No pit; one semi unloads directly through a 3,000-bu/h grain leg | Matches the described farm and preserves the queue bottleneck |
| Wet bin | Finite downstream buffer | 10,000-bu FIFO buffer | Buffer saturation is a central mechanism |
| Dryer | Persistent downstream processing bottleneck | Continuous FIFO service with moisture-dependent throughput | Sufficient to study coupling while the physical curve is being verified |
| STOP | Allows meaningful planned interruption | System scheduling action with 12-h eligibility and 8-h minimum | Distinguishes anticipatory scheduling from forced congestion |
| Weather, soybean, mud, commercial elevator | Affect real field availability and seasonal order | Excluded from the first mechanism experiment | Their inclusion would add exogenous uncertainty before the core mechanism is established |
| Grain quality and economics | Could change the optimal objective | Excluded; no shrink, energy price, quality loss, or labor opportunity value | First establish physical operating regimes; add economics only after the mechanism is clear |

### State-transition assumptions

- Opening to InteriorHarvest is a required phase transition, not a mode switch.
- Choosing the first mode for a field phase is not a switch.
- At most one voluntary production-mode switch is allowed in a field interior.
- A switch requires at least 30 min in the current interior mode and incurs 5 min with no grain production.
- Field change incurs 20 min and is permitted only after the current field is complete.
- The productive-work clock pauses during forced blocking, field movement, mode switching, and STOP; it resets only after a completed STOP.
- A STOP may occur only after 12 cumulative productive harvest hours and lasts at least 8 h.
- During STOP, existing grain can keep moving and the dryer can keep operating.

## 4. Strategy definitions and fairness

All four policies use the same fields, moisture realization, field order, physical model, observation update interval, feasible modes, one-switch limit, switch penalty, and STOP constraints. Prediction is the only additional information available to Predictive.

| Policy | Information used | Decision rule | Role in experiment |
|---|---|---|---|
| Fixed / Sequential | No system-state feedback | Shared field order; Opening uses 2C; interior uses 2C + 1GC; never voluntarily STOP | Non-adaptive reference |
| Static Threshold | Current occupancy/pressure only | `<40%`: 2C + 1GC; `40–80%`: 2C; `≥80%`: 1C; STOP at `≥95%` if eligible | Conventional heuristic reference; thresholds are not physical constants |
| Current-State Reactive | Current free storage, committed grain, current harvest/transport/dryer rates, and response time | Select the fastest mode that remains safe under current-state persistence; STOP only if every mode is unsafe and STOP is eligible | Main baseline |
| Finite-Horizon Predictive | All Reactive information plus modeled future field segments, moisture-dependent FIFO dryer evolution, field-specific transport capacity, and transitions over 48 h | Roll out each feasible immediate action, compare projected outcomes, execute only the first action, and replan | Main treatment |

The decision interval is 15 min for every state-dependent policy. This interval is separate from the one-minute event-simulation step.

### What Predictive does and does not know

Predictive knows only future quantities already supplied by the deterministic scenario:

- remaining opening and interior grain in the shared field sequence;
- field moisture and the corresponding provisional dryer rate;
- field-specific distance and effective semi transport rate;
- the current FIFO order of committed grain;
- known field-change, mode-change, and STOP constraints.

Thus, it can foresee a later dryer slowdown only when known higher-moisture grain is expected to reach the FIFO dryer. It does **not** currently know future weather, mud, machine failure, soybean windows, unexpected semi reassignment, or an operator changing dryer setup. Those require explicit scenario inputs in a later uncertainty experiment.

## 5. Physics-based Reactive trigger

Reactive contains a short current-state projection, because a competent operator also anticipates what happens if the present condition persists. It remains Reactive because it does not use a future trajectory or compare multi-step action sequences.

For action `a`, define:

- `H_a`: current harvest production rate under action `a`;
- `T`: current field-to-center transport capacity;
- `D`: current dryer throughput;
- `R_field`: free field/mobile storage available to the active configuration;
- `R_system`: free storage in the complete combine-cart-semi-wet-bin chain;
- `C`: grain already committed in combines, cart, and semis;
- `tau`: response time, currently travel + unload + mode-change time, bounded below by one simulation step.

Committed grain is represented as occupied storage:

`R_system = B_total - wet_bin_inventory - C`.

It must not be subtracted a second time. The safety margins are:

```text
M_field(a)  = R_field  - max(0, H_a - T) * tau
M_system(a) = R_system - max(0, H_a - D) * tau
M(a)        = min(M_field(a), M_system(a))
```

Decision logic:

1. Compute `M(a)` for every currently feasible production mode.
2. Choose the fastest mode with `M(a) >= 0`.
3. If no mode is safe and STOP is eligible, STOP.
4. If STOP is not yet eligible, continue with the slowest feasible mode and record any resulting forced block.
5. After the 8-h minimum STOP, resume when at least one mode is safe over the current response interval. The controller then cannot request another voluntary STOP until 12 new productive hours accumulate; forced blocking remains possible under a hard bottleneck.

This replaces the arbitrary statement “reduce production when the wet bin reaches 80%.” The 40/80/95% rule remains only in the separate Static Threshold reference.

## 6. Finite-horizon Predictive controller

“Finite horizon” means the controller looks ahead for a limited future duration rather than optimizing the complete harvest season.

At each 15-min decision time:

1. Enumerate the currently feasible immediate actions.
2. Simulate each action and downstream evolution for the next 48 h using a transparent aggregate forecast model.
3. Rank actions lexicographically by:
   1. remaining unharvested target grain;
   2. projected forced blocking;
   3. dryer idle time;
   4. terminal inventory.
4. Execute only the first action.
5. Observe the new current state 15 min later and repeat.

This resembles game look-ahead in the limited sense of evaluating consequences before taking the next step. It is not an AlphaGo-style learned policy or search tree. Version 1 is deterministic and auditable.

The full event simulation remains the evaluation environment. The controller's internal forecast is intentionally aggregate; it represents FIFO moisture, field-specific transport throughput, storage, known transitions, and operating constraints without duplicating the complete discrete-event simulator.

### Planned STOP is a valid predictive action, not an automatic loss

Predictive coordination is not defined as continuously matching the harvest
rate to the dryer rate. At a decision point it should compare the full
consequences of every feasible response to anticipated congestion, including
continuing at the current rate, switching production mode, and beginning a
legal Planned STOP. A Planned STOP may be beneficial if allowing the dryer and
logistics system to drain enables sufficiently faster subsequent harvesting or
reduces a separately justified operating cost. It should be penalized only for
modeled consequences such as elapsed time, restart cost, or missed operating
opportunities, not merely because the action is named STOP.

The completed 4x joint-capacity cases used no Planned STOP and instead selected
substantial intentional slowdown. This is evidence that the current controller
anticipates congestion, but it does not establish that continuous rate
smoothing is the best response. The present lexicographic rollout first
minimizes grain remaining at the 48-hour terminal point. An immediate 8-hour
STOP can therefore appear worse than continuous low-rate harvesting even when
post-horizon high-rate recovery might improve final completion. This potential
terminal effect must be diagnosed before refining the transition region.

The desired action comparison is joint and consequence-based:

```text
HARVEST at each feasible production mode
versus
PLANNED STOP under the shared 12-h eligibility and 8-h minimum-duration rules
```

If temporary grain-bag storage is introduced later, `DIVERT TO GRAIN BAG` must
enter the same action comparison for both Reactive and Predictive. The two
policies must share bag capacity, loading and retrieval rates, setup and
rehandling costs, and availability limits. Prediction may change when the bag
is selected, but it must not provide exclusive access to the bag.

## 7. Baseline parameters

| Parameter | Version-1 value |
|---|---:|
| Total field area | 650 ac across 10 fields |
| Yield | 200 bu/ac |
| Opening area | 15% per field |
| Opening 1C, rectangular / irregular | 1,415 / 1,077 bu/h |
| Opening 2C, rectangular / irregular | 2,831 / 2,154 bu/h |
| Interior 1C / 2C / 2C + 1GC | 1,600 / 3,200 / 4,000 bu/h |
| Combine storage | 300 bu per combine |
| Grain cart | 1 × 1,000 bu, aggregate |
| Semis | 3 × 1,000 bu |
| Semi unload | 20 min nominal |
| Grain leg | 3,000 bu/h |
| Receiving pit | None |
| Wet bin | 10,000 bu |
| Field moisture | 18–22%, paired across policies |
| Dryer curve | `D(M) = 2600 - 100M` bu/h, provisional |
| Field change | 20 min |
| Interior mode switch | Maximum 1 per field; 30-min dwell; 5-min penalty |
| STOP | Eligible after 12 productive h; minimum 8 h |
| Control decision interval | 15 min |
| Predictive horizon | 48 h |

## 8. Outcomes: define “benefit” without hiding the mechanism

There is no single arbitrary weighted benefit score in the exploratory phase.
Planned STOP is reported separately and is not automatically treated as a
loss. A policy may use a Planned STOP to reduce final completion time or other
explicitly justified operating costs. Conversely, reduced forced blocking is
not automatically a net benefit if it is purchased through excessive slowdown.

### Primary outcomes

- Harvest completion time.
- Forced blocking time.

### Secondary outcomes

- System completion time, including post-harvest drying.
- Planned STOP time and number/duration of STOPs.
- Dryer operating and idle time during planned STOP.
- Productive, active, blocked, and occupied machine-hours by resource.
- Number of interior mode switches.
- Semi trips and queue behavior.
- Wet-bin and mobile-storage trajectories.

The main estimands are paired differences under an identical scenario:

```text
Delta_harvest = harvest_time(Reactive) - harvest_time(Predictive)
Delta_block   = forced_block(Reactive) - forced_block(Predictive)
```

Positive values favor Predictive. Planned STOP is reported separately and is not automatically called a loss. A useful Predictive policy should convert avoidable forced blocking into earlier, constraint-aware actions without merely gaining more authority.

## 9. Preliminary mechanism experiment

Do not begin a large parameter sweep yet. Run a small operating-regime map designed to reveal the transition region.

### Main 3 × 3 map

Use maximum interior harvest rate `Q_harvest = 4,000 bu/h` and scale the entire provisional dryer curve while preserving its shape.

| Dryer scale | Dryer rate at 20% | `Q_harvest / D(20%)` | Intended regime |
|---:|---:|---:|---|
| 1× | 600 bu/h | 6.67 | Severe constraint |
| 4× | 2,400 bu/h | 1.67 | Intermediate / transition candidate |
| 8× | 4,800 bu/h | 0.83 | Dryer weak; overall system still receiving-limited |

Cross these with wet-bin capacity:

- 5,000 bu;
- 10,000 bu;
- 20,000 bu.

Keep all other inputs fixed and compare all four policies on the same moisture realization.

This dryer-only map does not create a true weak end-to-end condition because
the 3,000-bu/h grain leg and transport cycle remain below the 4,000-bu/h maximum
harvest rate. The separate joint-capacity diagnostic below resolves this
confounding for the regime-boundary check.

### Small working-window check

At dryer scale 4× and a 10,000-bu wet bin, test work-before-STOP values of 8, 12, and 16 productive hours. The default 12-h point is shared with the main map, so the complete preliminary set contains 11 unique scenario configurations, not a full sweep.

### Expected pattern to investigate, not assume

```text
weak constraint       -> Reactive approximately Predictive
intermediate constraint -> Predictive may add value
severe constraint     -> neither controller can remove the hard bottleneck
```

The experiment succeeds if it identifies and explains a regime boundary, even if Predictive does not improve the current baseline case.

### Completed joint-capacity diagnostic (2026-09-13)

A separate 1×/4×/10× experiment scaled the dryer curve, grain leg, and nominal
direct-unload service together and crossed them with 0.5×/1×/2× wet-bin
capacity. The 10× condition achieved maximum-harvest/effective-capacity ratios
of 0.93 at mean moisture and 1.00 at maximum moisture. Fixed, Reactive, and
Predictive were identical in this true weak condition, with zero blocking and
zero planned STOP. This supports the weak-regime boundary.

The 4× transition candidate exposed a controller issue: Predictive reduced
forced blocking but delayed harvest completion relative to Reactive. This must
be diagnosed through action-divergence traces before controller retuning or a
confirmatory sweep. The complete record is stored in
`results/current/joint_capacity_mechanism/RUN_RECORD.md`.

### Required plots after locating candidate cells

For one weak, one transition, and one severe case, plot:

- cumulative harvested and dried grain;
- wet-bin and mobile-storage inventory;
- operating mode and STOP intervals;
- forced block intervals;
- semi queue/availability.

The causal explanation to test is:

```text
known future constraint
-> earlier feasible action
-> changed inventory trajectory
-> less avoidable forced blocking
-> changed harvest completion time
```

## 10. Gates before confirmatory experiments

1. **Ask Aaron for the actual planting order** used for the modeled corn fields and whether it is known before harvest begins.
2. **Clarify dryer operation**: model/setup, whether throughput is set once or adjusted during the day, what triggers adjustment, and how quickly a change takes effect.
3. **Check literature/manual evidence** for the moisture-throughput curve, effective field-capacity treatment, opening fraction, and mode-change cost.
4. **Hand-audit one event trace** for semi departure, direct 20-min unload, wet-bin FIFO flow, STOP, and mass conservation.
5. **Run the preliminary mechanism experiment**, inspect trajectories, and identify whether a transition region exists.
6. Only then decide the paper's confirmatory parameter ranges, uncertainty model, replications, and statistical reporting.

## 11. Interpretation paths

- If Reactive and Predictive are similar everywhere, the modeled future information is not decision-relevant or the action set is too weak. That is a boundary result, not a reason to quietly give Predictive more authority.
- If Predictive helps only near a capacity boundary, the main contribution is an operating map explaining when predictive coordination is worthwhile.
- If Predictive loses in some regimes, inspect forecast mismatch, horizon/end effects, unnecessary STOP, and objective ordering before changing parameters.
- If both fail under severe constraints, report the infrastructure limit; control cannot overcome insufficient physical capacity.
- If Static Threshold performs close to Reactive, the physical rule may not yet add enough information; compare their trigger timing and sensitivity rather than relying only on completion time.

The exploratory phase ends when these interpretation paths can be supported by event traces and a small, defensible regime map—not when dozens of figures have been generated.
