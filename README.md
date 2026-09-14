# Harvest Coordinate Simulator

这是一个离散事件的玉米收获—运输—烘干协调模拟器。当前探索阶段围绕一个冻结的研究问题：

> **When does predictive coordination provide additional value over current-state reactive coordination under downstream constraints?**

完整的研究边界、证据状态、策略定义和 preliminary mechanism experiment 见 [autonomous_harvest_experiment_design.md](autonomous_harvest_experiment_design.md)。当前数据支持的结论、尚未成立的主张、transition-region 解释和 receiving-pit 讨论集中记录在 [PRELIMINARY_FINDINGS.md](PRELIMINARY_FINDINGS.md)。

## 四种策略

- `fixed`：固定田块顺序；Opening 用 2C，Interior 用 2C+1GC；不主动 STOP。
- `threshold`：普通 40%/80%/95% occupancy-threshold reference。
- `reactive`：只根据 current state 的剩余 buffer、committed grain、当前 harvest/transport/dryer rate 和 response time 决策。
- `predictive`：在相同 action space 和约束下，对候选当前 action 做 48 h rolling-horizon 推演。

`sequential` 和 `adaptive` 仍可作为兼容命令使用，分别映射到 `fixed` 和 `predictive`；新实验与结果应使用四个正式名称。

所有策略使用同一田块顺序。实际 planting order 尚未取得，因此默认顺序是明确标注为暂定的 far-to-near 场景，而不是声称 Aaron 实际按距离收割。

## 当前基线

| 项目 | 值 |
|---|---:|
| Field area | 10 fields, 650 ac total |
| Opening | 每田 15%，可选 1C 或 2C |
| Interior rates | 1C 1,600；2C 3,200；2C+1GC 4,000 bu/h |
| Interior mode switch | 每田最多 1 次；30 min dwell；5 min penalty（暂定） |
| Combine tank | 300 bu/combine |
| Grain cart | aggregate 1,000 bu（暂定） |
| Semis | 3 × 1,000 bu |
| Receiving pit | 无 |
| Grain leg | 3,000 bu/h；满载 semi 名义卸粮 20 min |
| Wet bin | 10,000 bu |
| Dryer | continuous FIFO；暂用 `D(M)=2600-100M`，`M=18–22%` |
| STOP | 累计 productive harvest ≥12 h 才可开始；至少 8 h |
| Control update | 每 15 min |
| Predictive horizon | 48 h |

STOP 是 system scheduling action，不是 field mode：只停止新粮生产，combine/cart/semi 中已有粮食仍可转运，grain leg 和 dryer 继续运行。Planned STOP 与 forced blocking 分开报告。

## 运行

在项目根目录运行：

```bash
python run_baseline.py --policy fixed
python run_baseline.py --policy threshold
python run_baseline.py --policy reactive
python run_baseline.py --policy predictive
python run_experiments.py
python -m unittest discover -s tests -v
```

`run_baseline.py` 运行一个策略并保存完整数据：

- `metrics.json`
- `timeseries.csv`
- `transport_events.csv`
- `daily_machine_hours.csv`
- `overview.svg`
- `first_3h.svg`
- scenario-level `field_scenario.csv`

`run_experiments.py` 在同一个 moisture realization 上比较四种策略，输出：

- `policy_comparison.csv`
- `machine_hours_summary.csv`
- `daily_machine_hours_comparison.csv`
- 每个策略的 `overview.svg`

结果默认写入 `results/current/` 下带完整参数的场景目录，不覆盖旧命名的结果。

## Preliminary mechanism experiment

```bash
python run_experiments.py --mechanism
```

这不是 full sweep。脚本运行：

- dryer curve scale `1× / 4× / 8×`；
- wet-bin capacity scale `0.5× / 1× / 2×`；
- 在中间 capacity/buffer 条件下附加 `8 / 12 / 16 h` work-before-STOP 检查。

共 11 个独立 scenario configurations，每个都配对运行四策略。汇总写入 `results/current/mechanism/mechanism_summary.csv`，包含 `Q_harvest / mean dryer rate`，用于寻找 weak → transition → severe constraint 区域。

若要同步放大dryer、grain leg和名义直接卸粮能力，并保留所有完整轨迹：

```bash
python run_experiments.py --joint-capacity-mechanism
```

该实验独立写入 `results/current/joint_capacity_mechanism/`，不会覆盖上述
dryer-only结果。每个策略保存metrics、timeseries、transport events、daily
machine-hours和overview图；`RUN_RECORD.md`记录精确参数、结果和限制。

## 常用覆盖参数

```bash
python run_baseline.py --policy predictive `
  --wet-bin-capacity 10000 `
  --grain-leg-rate 3000 `
  --semi-count 3 `
  --seed 42 `
  --moisture-min 18 `
  --moisture-max 22 `
  --dryer-intercept 2600 `
  --dryer-slope 100 `
  --opening-fraction 0.15 `
  --interior-1c-rate 1600 `
  --interior-2c-rate 3200 `
  --interior-2c-1gc-rate 4000 `
  --mode-dwell-min 30 `
  --max-mode-switches 1 `
  --mode-switch-time-min 5 `
  --decision-interval-min 15 `
  --prediction-horizon-h 48 `
  --work-before-stop-h 12 `
  --minimum-stop-h 8 `
  --field-order-rule far_to_near `
  --name-suffix trial
```

以上是 PowerShell 的续行写法；也可以写成一行。

## 物理流和守恒

```text
combine tanks -> aggregate grain cart -> semis
              -> direct grain-leg unload -> wet bin -> FIFO dryer
```

每个记录时刻检查：

```text
harvested grain
  = combine + grain cart + all semis + wet bin + dried grain
```

无 receiving pit。湿粮仓满时，semi 卸粮会受 dryer 出流限制；semi 清空后才返田。田块距离同时影响真实往返事件与 Predictive 内部的未来 transport-capacity estimate。

## 结果解释

优先看：

- `harvest_completion_time_h`
- `forced_blocking_time_h`
- `planned_stop_time_h`
- `system_completion_time_h`
- 设备 active/occupied machine-hours
- inventory、queue、mode 和 STOP 的时间轨迹

不要把计划 STOP 自动解释成损失，也不要只用一个百分比宣称 Predictive 更好。当前阶段的目标是找出 action timing 如何改变库存和 forced blocking，以及这种差异在哪个 constraint regime 出现。

## 当前未确认边界

- modeled fields 的真实 planting order；
- dryer 是否会在一天中改变设置、变化响应时间和适用吞吐曲线；
- 15% Opening、1,000-bu aggregate cart、30-min dwell 和 5-min switch penalty 的依据；
- soybean、weather、mud、commercial elevator 和 equipment failure 均未进入 version 1。

在上述问题完成追问和文献检查前，机制实验只应称为 preliminary/exploratory results。
