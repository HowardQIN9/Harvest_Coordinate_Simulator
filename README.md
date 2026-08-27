# Harvest_Coordinate_Simulator

Python MVP implementing the requirements in `README_harvest_simulation_MVP.md`.

## Run

```bash
python3 run_baseline.py --policy sequential
python3 run_baseline.py --policy adaptive
python3 run_baseline.py --policy adaptive --name-suffix interior1500
python3 run_experiments.py
python3 -m unittest discover -s tests
```

Single runs write metrics JSON, a time-series CSV, and a dependency-free four-panel
SVG plot to `results/`.
The experiment runner compares both policies at downstream rates 600, 800, 900,
1000, 1100, and 1200 bu/h and writes
`results/sensitivity_comparison.csv`.

The model conserves grain: harvested grain always equals processed grain plus the
current equivalent downstream buffer. Grain that cannot enter a full buffer is not
discarded; only the feasible fraction of the minute is productive and the remainder
is recorded as downstream-induced blocked time.

## Current harvest throughput

At 200 bu/ac, interior harvest is currently 1,500 bu/h (7.5 ac/h). Opening rates
retain the original proportions: rectangular opening is 1,326.92 bu/h
(6.6346 ac/h), and irregular opening is 1,009.62 bu/h (5.0481 ac/h).
