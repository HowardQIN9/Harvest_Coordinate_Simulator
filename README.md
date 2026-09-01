# Harvest Coordinate Simulator

One combine, configurable semi trucks, and a grain center with continuous processing.
The current model below supersedes the historical equivalent-buffer model in
`README_harvest_simulation_MVP.md`.

## Run

From this project directory in PowerShell:

```powershell
python run_baseline.py --policy sequential
python run_baseline.py --policy adaptive
python run_baseline.py --policy adaptive --gc-rate 1500
python run_experiments.py
python -m unittest discover -s tests -v
```

Only Python's standard library is required. Results go to `results/semi_transport/`
by default, keeping older simulation outputs separate. Open SVG files in a browser.
Each run produces:

- `*_plots.svg`: full run, seven panels for field grain, semi loads, semi states,
  GC inventory, combine state, harvested acreage, and cumulative grain.
- `*_first3h.svg`: the first three hours, making individual transport events visible.
- `*_metrics.json`: summary metrics.
- `*_timeseries.csv`: inventories and states at time-step and event boundaries.
- `*_transport_events.csv`: loading, departure, GC arrival, and field return events.

`--output-dir` and `--name-suffix` customize output paths. `--downstream-rate` is
retained as an alias of `--gc-rate`; it now changes GC processing only. Without a
rate flag, the runner uses the GC rate in `SimulationConfig`.

## Physical model and parameters

Edit `harvest_sim/config.py`:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `combine_tank_capacity_bu` | 500 | Combine grain tank capacity |
| `semi_count` | 1 | All trucks initially empty at the field |
| `semi_capacity_bu` | 1000 | Capacity of each semi |
| `semi_gc_arrival_h` | 20 / 60 | Time from departure to GC delivery |
| `semi_round_trip_h` | 45 / 60 | Time from departure to empty return |
| `grain_center_processing_rate_buph` | 1000 | Continuous GC processing rate |
| `dt_h` | 1 / 60 | Maximum time step; events can shorten it |
| `switch_time_h` | 20 / 60 | Combine travel time between fields |

There is no ground storage. Grain enters the combine tank while harvesting.
When the tank is full, it transfers grain instantly to an on-site semi with room,
prioritizing the most-loaded semi. Normally two 500-bu transfers fill one truck.
A full semi departs immediately. With no truck available, the combine can harvest
until its own 500-bu tank is full; only then is the combine `BLOCKED`.

Each semi has independent states: `AT_FIELD -> TO_GC -> RETURNING -> AT_FIELD`.
For a departure at time t, GC receives the truck's grain at t + 20 min, and the
empty truck returns at t + 45 min. Loading and unloading consume no time. GC
inventory has no capacity limit: a truck unloads immediately and returns without
waiting for grain processing. GC processes at its configured rate whenever inventory is
positive, and stops when empty. Processing never consumes grain before it arrives.

Assumptions for field changes: trucks at the field follow the combine without a
separate relocation delay; returning trucks serve the combine's current field.
The model does not track road distances between individual fields. Semi trips and
GC processing continue during combine field switching and blocking.

After harvest, remaining combine grain is transferred when a semi is available,
and the last truckload may be partial. The simulation continues until all grain
has been processed; it need not wait for already-empty trucks to return afterward.

## Grain accounting and event records

At every recorded instant:

```text
harvested grain = combine tank
                + grain on semis at the field
                + grain on semis travelling to GC
                + GC inventory
                + processed grain
```

`field_grain_bu` is the sum of grain physically on the combine and on-site semis;
it is NOT an extra storage pool. It drops when a loaded truck departs.
`in_transit_grain_bu` drops and `grain_center_inventory_bu` jumps when a truck
arrives at GC. Empty returning trucks carry no grain.

Time-series rows with positive `duration_h` describe the interval ENDING at
`time_h`. Instantaneous events have zero duration and may share a timestamp with
the preceding row. Preserve these rows to plot true jumps and conserve grain.

## Field completion and adaptive selection

`field_geometries` defines the field set and initial order. The default is three
rectangular and three irregular fields, each with 10 acres of opening and 40 acres
of interior harvest. Both policies must finish `Opening` and `InteriorHarvest`
before changing fields, even while blocked. Within-field phase transitions are
free. Six fields require five field switches, totaling 100 minutes.

Sequential visits fields in configuration order. Adaptive selects a new field
only after the current field is complete. Its signal is receiving pressure:

```text
available space = free combine tank space + free space on on-site semis
receiving pressure = 1 - available space
                     / (combine capacity + semi count * semi capacity)
```

Away trucks provide no receiving space, including empty trucks still returning.
At pressure >= 0.80, prefer the available task with the lowest configured harvest
rate; at pressure <= 0.40, prefer the highest rate; in between, use the current
feasible task or configuration order. This is a simple heuristic, not a prediction
of truck return times. The old minimum-task-duration setting is unnecessary under
the hard whole-field constraint and has been removed.

The current `interior_capacity_acph` is **7.5 ac/h**, giving **1500 bu/h** at
200 bu/ac. Opening capacities remain independent: rectangular opening is about
1326.92 bu/h and irregular opening about 1009.62 bu/h. Editing interior capacity
does not rescale opening capacities.

## Completion metrics and experiments

- `harvest_completion_time_h`: when all fields are harvested.
- `total_completion_time_h`: retained alias of harvest completion time.
- `system_completion_time_h`: when all harvested grain is processed at GC.
- `post_harvest_time_h`: extra time for final delivery and processing; not combine
  blocked time and not included in combine utilization.
- `*_at_harvest_completion_bu`: inventories at harvest completion, before final
  flushing. Final total processed grain includes the post-harvest period.

Harvest completion time equals productive harvesting + field switching + blocking.
`run_experiments.py` sweeps GC rates 600, 1000, 1500, 2000, 2500, and 3000 bu/h for
both policies. With unlimited GC inventory and instantaneous unloading, changing
GC speed changes inventory and system completion time, not truck availability or
combine blocking. To explore transport-induced blocking, change harvest rates,
truck count/capacity, round-trip time, or combine tank size.

Tests cover exact 20/45-minute events, simultaneous deliveries, coarse time steps,
blocking only with a full tank and no semi, resumption on return, GC independence,
final partial loads, field completion, and grain conservation.
