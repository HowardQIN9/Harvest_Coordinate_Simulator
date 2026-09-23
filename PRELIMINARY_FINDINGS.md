# Preliminary Findings and Current Interpretation

> [!IMPORTANT]
> **Historical findings record.** The results and controller descriptions in
> this file come from superseded experiments; they must not be combined with
> the current validation results. Use [`README.md`](README.md) and
> `results/validation/` for the active methodology and evidence.

Last updated: 2026-09-13

## 1. Status and frozen research question

This document records the conclusions that are currently supported by the
simulation, the interpretations that remain provisional, and the claims that
cannot yet be made. It is an exploratory research record, not a final results
section.

The frozen research question is:

> **When does predictive coordination provide additional value over
> current-state reactive coordination under downstream constraints?**

The central scientific comparison is Current-State Reactive versus
Finite-Horizon Predictive. Fixed and Static Threshold are interpretive
references.

## 2. Terms that must remain distinct

### Forced blocking

Forced blocking is a constraint-induced interruption. The harvesting system
wants to continue producing grain, but the grain cart, semis, grain leg, wet
bin, or other downstream components cannot accept more grain. It is not a
controller-selected action. The dryer and some logistics components may
continue operating while grain production is blocked.

Recommended paper terms are **constraint-induced forced blocking** or
**unplanned harvest interruption**. Avoid calling it `forced STOP`, because
`STOP` has a separate formal meaning in the model.

### Planned STOP

Planned STOP is a controller-selected scheduling action. It becomes eligible
after at least 12 productive harvest hours and must last at least 8 hours. New
grain production stops, while grain already in the combine tanks, grain cart,
semis, wet bin, and dryer can continue moving or processing.

### Intentional slowdown

Intentional slowdown is an active decision to use a production mode below the
fastest feasible mode. Grain production continues at a lower rate. Its purpose
is to shape inflow before downstream storage or processing becomes saturated.

## 3. Meaning of the 1x, 4x, and 10x conditions

The labels are joint downstream-capacity multipliers. They do not mean one,
four, or ten machines, and they are not dryer-only multipliers. The experiment
scales the provisional dryer curve and grain-leg rate together and shortens
nominal semi-unloading time by the same factor. This prevents the grain leg or
unloading process from silently replacing the dryer as the bottleneck.

| Joint scale | Dryer at 18/20/22% moisture (bu/h) | Grain leg (bu/h) | Nominal semi unload | Effective capacity at mean/worst moisture (bu/h) | Maximum harvest/effective ratio, mean/worst | Intended regime |
|---:|---:|---:|---:|---:|---:|---|
| 1x | 800 / 600 / 400 | 3,000 | 20 min | 600 / 400 | 6.67 / 10.00 | Severe constraint |
| 4x | 3,200 / 2,400 / 1,600 | 12,000 | 5 min | 2,400 / 1,600 | 1.67 / 2.50 | Intermediate-to-strong constraint |
| 10x | 8,000 / 6,000 / 4,000 | 30,000 | 2 min | 4,286 / 4,000 | 0.93 / 1.00 | Weak/nonbinding constraint |

The maximum harvest rate is 4,000 bu/h. The 10x condition is an intentionally
constructed nonbinding boundary, not a recommendation for Aaron's farm. The 4x
condition is useful as a mechanism scenario but may be unrealistic as a
physical upgrade package.

A more transferable description of constraint severity is

```text
rho = maximum harvest rate / effective downstream capacity
```

When `rho <= 1`, downstream capacity is approximately nonbinding. Values only
slightly above one are candidate transition conditions. Large values represent
severe chronic overload.

## 4. Completed joint-capacity experiment

The experiment contains:

- 3 joint downstream-capacity levels: 1x, 4x, and 10x;
- 3 wet-bin capacities: 5,000, 10,000, and 20,000 bu;
- 4 policies: Fixed, Static Threshold, Current-State Reactive, and
  Finite-Horizon Predictive;
- 9 paired scenarios and 36 policy runs;
- one common field order, moisture realization, and random seed 42 within each
  comparison;
- a 48-hour Predictive horizon and 15-minute control interval.

All 36 runs retained metrics, complete time series, transport events, daily
machine-hour records, and overview plots. All runs ended with zero mass-balance
error. These checks support implementation consistency but do not by themselves
establish external validity or controller optimality.

## 5. Main Reactive-versus-Predictive results

Predictive harvest benefit is defined as:

```text
Reactive harvest completion time - Predictive harvest completion time
```

A positive value favors Predictive. Blocking reduction is Reactive forced
blocking minus Predictive forced blocking.

| Joint scale | Wet bin (bu) | Reactive harvest (h) | Predictive harvest (h) | Predictive harvest benefit (h) | Blocking reduction (h) |
|---:|---:|---:|---:|---:|---:|
| 1x | 5,000 | 196.48 | 197.55 | -1.07 | 23.04 |
| 1x | 10,000 | 189.55 | 188.17 | +1.38 | 26.13 |
| 1x | 20,000 | 172.60 | 174.26 | -1.66 | 31.87 |
| 4x | 5,000 | 49.12 | 53.60 | -4.48 | 8.90 |
| 4x | 10,000 | 47.47 | 54.76 | -7.29 | 8.51 |
| 4x | 20,000 | 43.09 | 54.71 | -11.63 | 4.26 |
| 10x | 5,000 | 38.35 | 38.35 | 0.00 | 0.00 |
| 10x | 10,000 | 38.35 | 38.35 | 0.00 | 0.00 |
| 10x | 20,000 | 38.35 | 38.35 | 0.00 | 0.00 |

The complete numeric output is in
`results/current/joint_capacity_mechanism/joint_capacity_summary.csv`. The run
construction and validation record is in
`results/current/joint_capacity_mechanism/RUN_RECORD.md`.

## 6. Findings currently supported by the data

### 6.1 Prediction has no additional value when downstream capacity is nonbinding

At 10x, Fixed, Reactive, and Predictive all complete harvest in 38.35 hours,
with zero forced blocking and zero planned STOP, for all three wet-bin sizes.
The result supports the boundary mechanism:

```text
nonbinding downstream capacity
    -> the fastest current action remains safe
    -> Reactive and Predictive choose the same actions
    -> future information has no additional operational value
```

This conclusion is strong within the tested deterministic scenario. It is not
yet a general claim across field orders, moisture realizations, or forecast
errors.

### 6.2 Predictive coordination consistently changes how congestion is handled

Across all 1x and 4x conditions, Predictive reduces forced blocking. The
reduction is 23-32 hours at 1x and 4-9 hours at 4x. Therefore, future
information is decision-relevant in binding conditions and is not merely a
renamed version of the Reactive controller.

The mechanism supported by the current results is:

```text
forecast future downstream congestion
    -> reduce production inflow before capacity is exhausted
    -> replace some constraint-induced blocking with planned flow regulation
```

This establishes anticipatory behavior. It does not alone establish higher
overall system efficiency.

### 6.3 Severe capacity constraints dominate policy choice

At 1x, Predictive reduces forced blocking substantially, but its harvest-time
effect relative to Reactive ranges only from a 1.38-hour improvement to a
1.66-hour loss. System completion remains near 206-207 hours because long-run
dryer throughput dominates. Predictive mainly reorganizes waiting; it cannot
overcome the physical processing limit.

At 1x, Predictive also uses about 32 hours of Planned STOP, compared with about
8 hours for Reactive. The severe-regime result therefore reflects both planned
stopping and rate selection, not proactive slowdown alone.

### 6.4 Static Threshold is useful as a reference but not as the main baseline

At 10x, Static Threshold requires 44.29 hours while Fixed, Reactive, and
Predictive require 38.35 hours. It introduces approximately 26.2 hours of
intentional slowdown even though the end-to-end system is nonbinding. This
shows that an occupancy-only rule can overreact. It does not prove that every
threshold controller is poor; it shows why the physics-based Reactive policy
is the more defensible main baseline.

## 7. Central trade-off exposed by the 4x, 10,000-bu case

| Metric | Reactive | Predictive | Interpretation |
|---|---:|---:|---|
| Harvest completion time | 47.47 h | 54.76 h | Predictive is 7.29 h slower |
| System completion time | 52.22 h | 55.21 h | Predictive is about 2.98 h slower |
| Forced blocking | 8.64 h | 0.13 h | Predictive removes 8.51 h of blocking |
| Planned STOP | 0 h | 0 h | The difference is not caused by STOP |
| Intentional slowdown | 2.00 h | 39.30 h | Predictive regulates inflow much more aggressively |
| Interior mode switches | 1 | 9 | Predictive changes mode much more frequently |
| Total active machine-hours | 201.00 h | 163.43 h | Predictive uses 37.57 fewer active machine-hours |
| Total occupied machine-hours | 272.87 h | 258.67 h | Predictive uses 14.21 fewer occupied machine-hours |

This case directly supports the following mechanism statement:

> Predictive coordination anticipates downstream congestion and replaces
> constraint-induced forced blocking with proactive production-rate
> adjustment.

In this case, calling the reduction a `forced stop reduction` is understandable
in ordinary language, but `forced blocking reduction` is more precise. The
harvesting operation is forced to pause; the entire downstream system does not
necessarily stop.

The result also shows that the definition of benefit is not yet closed:

- If benefit means earlier harvest completion, Predictive loses.
- If benefit means earlier system completion, Predictive loses.
- If benefit means fewer unplanned interruptions, Predictive wins.
- If benefit means fewer active or occupied machine-hours, Predictive may win.

The data therefore show a trade-off rather than unconditional policy
superiority. The value of replacing forced blocking with controlled slowdown
depends on the operational cost of an unplanned interruption relative to the
cost of a longer, smoother harvest period.

### 7.1 Revised interpretation of Planned STOP and rate smoothing

The absence of Planned STOP in the 4x cases is not itself desirable. Planned
STOP is a legitimate predictive action. If stopping for at least 8 hours lets
the dryer and logistics system drain, supports other useful farm work, and
enables sufficiently faster subsequent harvesting, it can reduce final
completion time or operating cost and should be counted as a benefit.

The Predictive controller must not be defined as a controller that slowly
matches production to the dryer. Its task is to compare the final consequences
of all currently feasible congestion responses:

```text
continue at high rate
switch to a lower production mode
begin a legal Planned STOP
future extension: divert grain to a grain bag
```

The current rollout first minimizes unharvested grain remaining at the fixed
48-hour terminal point. Because an immediate Planned STOP produces no new grain
for at least 8 hours, continuous low-rate harvesting can look better inside the
horizon even if stopping, draining the system, and later harvesting quickly
would improve completion after the horizon. STOP is also disfavored as a final
tie-break when the other projected outcomes are identical. The 4x result may
therefore contain a finite-horizon terminal effect rather than evidence that
rate smoothing is optimal.

The supported conclusion should consequently remain narrow:

> Predictive identified future congestion and converted forced blocking into
> proactive flow regulation, but the current experiment has not established
> whether intentional slowdown or Planned STOP is the better anticipatory
> response.

Future controller evaluation should compare joint actions using projected
harvest completion, system completion, forced-interruption cost, and other
explicitly justified operating consequences. Planned STOP should not receive
an arbitrary penalty. Any restart, labor, weather-window, or opportunity cost
must be modeled from farmer feedback or literature.

This principle is essential before introducing grain bags. A grain bag is a
third congestion response based on temporary storage rather than rate control
or time shifting. If added, Reactive and Predictive must receive the same
`DIVERT TO GRAIN BAG` action, capacity, loading and retrieval rates, setup cost,
rehandling cost, and use limits. Prediction may create value through earlier
selection of that shared action; it must not win through a larger action space.

## 8. Strength of the current conclusion chain

| Logic link | Evidence status | Current judgment |
|---|---|---|
| Multi-machine harvesting requires coordination between production and downstream capacity | Supported by the modeled physical flow and farmer-practice motivation | Strong |
| Downstream constraint strength changes policy outcomes | Large differences across 1x, 4x, and 10x | Strong |
| Prediction has no additional value under nonbinding conditions | Identical 10x outcomes across three buffer sizes | Strong within the tested scenario |
| Prediction can reduce forced blocking through anticipatory control | Consistent reductions in every 1x and 4x condition | Strong |
| Severe constraints cannot be overcome by coordination alone | Completion-time effects at 1x are small and unstable while physical throughput dominates | Moderately strong |
| Reduced forced blocking automatically means higher system value | Conflicts with completion-time results | Not established |
| Predictive provides net value in an intermediate regime | Current 4x controller is slower in all three buffer conditions | Not established |
| Results generalize to real farms | One seed and several provisional physical assumptions | Not established |

As an exploratory study, the logic is strong enough to identify the next
question. As a final paper claim that Predictive is superior, the chain is not
yet complete.

## 9. Interpretation of the transition region

The original three-regime hypothesis remains useful:

```text
weak constraint        -> Reactive approximately Predictive
intermediate constraint -> future information may change useful actions
severe constraint      -> physical capacity dominates and no policy can rescue throughput
```

Only the weak-regime prediction is currently cleanly confirmed. The severe
regime supports physical-capacity dominance, but Predictive may still change
machine-hour use and the form of waiting. The intermediate regime remains a
diagnostic target rather than a confirmed Predictive-benefit region.

The 4x condition should remain as a mechanism boundary point, but it should not
be presented as a realistic upgrade recommendation. A practical transition
search should use farmer- and literature-supported limits and then refine the
constraint level incrementally.

Capacity scaling alone may not be the most realistic way to enter the
transition region. Because the dryer runs continuously while field harvesting
has a limited daily window, a useful daily load ratio is:

```text
daily load ratio
    = maximum harvest rate * harvest hours per day
      / (dryer rate * 24 hours per day)
```

Realistic working windows, overnight wet-bin recovery, field transitions,
weather, and planned crop switching may create a transition without requiring
a fourfold physical upgrade.

The recommended later search is:

1. Retain 1x, 4x, and 10x as mechanism/sanity-check points.
2. Establish plausible capacity and working-window ranges from Aaron and the
   literature.
3. After diagnosing the current Predictive controller, run a coarse search
   over the plausible range.
4. Use smaller increments only around the first region where action choices or
   outcomes diverge.
5. If Predictive has no completion-time benefit anywhere in the plausible
   range, report that boundary instead of increasing capacity until it wins.

## 10. Receiving-pit interpretation

The current system has no receiving pit:

```text
combine tanks -> aggregate grain cart -> semis
              -> direct grain-leg unload -> wet bin -> FIFO dryer
```

A pit would add:

```text
semis -> receiving pit -> grain leg -> wet bin -> dryer
```

A pit can improve absolute system performance when truck unloading or truck
turnaround is limiting. It can let a semi dump quickly, return to the field,
and absorb short arrival peaks. It does not increase the dryer's long-run
processing rate. If average inflow continues to exceed dryer output, the pit
eventually fills and only delays the downstream bottleneck.

The expected effects are therefore:

- potentially less transport-induced blocking and faster semi turnaround;
- limited benefit when chronic dryer capacity is the dominant bottleneck;
- little effect when downstream capacity is already nonbinding;
- possibly a smaller relative Predictive advantage because additional physical
  buffer gives Reactive more time to respond.

This suggests a possible later insight: physical buffering and predictive
information may substitute for one another. However, because Aaron's current
system has no pit, the main model should remain no-pit. A pit should be a
separate infrastructure sensitivity, not silently included in the main policy
comparison.

A pit is also unlikely to correct the current 4x Predictive slowdown. In that
condition, the grain leg and unloading process are already fast, Planned STOP
is zero, and the delay comes primarily from aggressive intentional slowdown
and mode switching.

## 11. Claims that cannot yet be made

The current results do not support the following claims:

- Predictive coordination generally improves harvest or system completion
  time.
- The 4x condition proves that prediction is harmful.
- A robust intermediate Predictive-benefit region has been identified.
- Forty-eight hours is the correct or optimal prediction horizon.
- The observed results generalize across field orders, moisture sequences,
  forecast errors, farms, or seasons.
- Dryer, grain leg, transport, and buffer effects have been individually
  identified by the joint-capacity experiment.
- A stable percentage improvement is ready to report.

## 12. Current safe conclusion

> In the tested deterministic scenarios, predictive coordination provides no
> additional value when the end-to-end downstream system is nonbinding. As
> downstream constraints become active, the predictive controller consistently
> reduces forced blocking by anticipating future congestion. However, this
> reduction does not yet translate into a robust improvement in harvest or
> system completion time because the controller replaces forced waiting with
> planned stops or intentional rate reduction. Under extremely severe
> constraints, physical downstream capacity dominates policy choice. The
> intermediate regime therefore remains the key diagnostic region, where the
> definition of operational benefit and the predictive controller's horizon and
> objective must be resolved before confirmatory experiments.

中文表述：

> 在当前确定性场景中，当端到端下游系统不构成约束时，Predictive 不提供
> 额外价值。当下游约束生效时，Predictive 能够预见未来拥堵并减少 forced
> blocking，但目前主要是将被迫等待转换为 Planned STOP 或主动降速，尚未
> 稳定缩短收获或系统完成时间。在极强约束下，物理吞吐能力主导策略表现。
> 因此，中等约束区仍是最关键的诊断区域。在进入正式验证实验前，需要先
> 明确 operational benefit，并检查 Predictive 的 horizon、objective 和内部
> forecast 是否导致了不必要的降速。

## 13. Immediate unresolved questions

Before a confirmatory sweep, the following questions remain open:

1. In the 4x, 10,000-bu condition, why does Predictive select about 39.3 hours
   of intentional slowdown and nine switches?
2. Should primary benefit be harvest completion, system completion, unplanned
   interruption reduction, machine-hours, or an explicitly justified cost
   combination?
3. What are the farmer- and literature-supported ranges for dryer throughput,
   field working window, opening fraction, switching cost, and field order?
4. How does forecast error change the value of the deterministic 48-hour
   look-ahead?
5. Does a receiving pit substitute for predictive information, and is that
   question inside the final paper scope or only a later sensitivity study?
