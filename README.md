# Harvest Coordinate Simulator

离散事件收获协调模拟器：收获端可采用 1C、2C 或 2C+1GC，一辆半挂车连接接收坑、grain leg、wet bin 和 dryer。模型比较固定高强度作业与“田块选择 + Interior模式控制”的预测式 Adaptive。

## 运行

在项目目录运行：

```bash
python3 run_baseline.py --policy sequential
python3 run_baseline.py --policy adaptive
python3 run_experiments.py
python3 -m unittest discover -s tests -v
```

只使用 Python 标准库。收割从 `t=0` 开始，每个24小时周期运行14小时、计划停工10小时；停工期间semi、grain leg和dryer继续工作。结果按 `sequential/` 和 `adaptive/` 分目录：

- `metrics.json`：汇总指标；
- `timeseries.csv`：每个时间步和事件边界的库存与状态；
- `transport_events.csv`：装车、出发、到站、开始/完成卸货和返田事件；
- `daily_machine_hours.csv`：按24小时拆分的C、GC和semi active/occupied machine-hours；
- `overview.svg`：完整仿真图；
- `first_3h.svg`：前三小时细节图；
- `field_scenario.csv`：所有策略共用的田块面积、形状、距离、moisture 和 dryer rate；
- `policy_comparison.csv`：两种策略的汇总对比。
- `machine_hours_summary.csv`：两种策略的全季设备小时汇总。
- `daily_machine_hours_comparison.csv`：两种策略逐日设备小时的合并对比。

旧模型产生的结果保留在 `results/archive/`，目录说明见 `results/README.md`。

本次14h/10h场景可单独写入新目录、避免覆盖连续收割结果：

```bash
python3 run_baseline.py --policy sequential --output-dir results/harvest_schedule_14h_on_10h_off
python3 run_baseline.py --policy adaptive --output-dir results/harvest_schedule_14h_on_10h_off
python3 run_experiments.py --output-dir results/harvest_schedule_14h_on_10h_off
```

所有图使用 20pt 字体，子图标题放在左侧竖排，并为标题、坐标和图例预留独立空间。

命令行可覆盖下游参数：

```bash
python3 run_baseline.py --policy adaptive \
  --wet-bin-capacity 10000 \
  --grain-leg-rate 3000 \
  --seed 42 \
  --moisture-min 18 \
  --moisture-max 22 \
  --dryer-intercept 2600 \
  --dryer-slope 100 \
  --interior-1c-rate 2000 \
  --interior-2c-rate 4000 \
  --interior-2c-1gc-rate 5000 \
  --mode-dwell-min 30 \
  --max-mode-switches 2 \
  --harvest-work-h 14 \
  --harvest-rest-h 10 \
  --name-suffix trial
```

## 默认田块

表中的运输时间是单程分钟，不是英里。返程时间与去程相同。

| Field | Area (ac) | Geometry | One-way transport (min) |
| --- | ---: | --- | ---: |
| F1 | 50 | irregular | 5 |
| F2 | 80 | rectangular | 15 |
| F3 | 50 | irregular | 20 |
| F4 | 80 | rectangular | 5 |
| F5 | 50 | irregular | 15 |
| F6 | 80 | rectangular | 20 |
| F7 | 50 | irregular | 15 |
| F8 | 80 | rectangular | 15 |
| F9 | 50 | irregular | 15 |
| F10 | 80 | rectangular | 15 |

总面积为 650 ac；按 200 bu/ac 计算，总粮量为 130,000 bu。新增的 F7–F10 中，两个为规则田、两个为不规则田，单程运输时间均为中间档 15 分钟。每块田必须按 `Opening → Interior` 完成后才能换田；换田耗时 20 分钟。因此十块田固定产生九次换田，共 180 分钟。

每个田块的 moisture 是场景参数。在 simulation 初始化时独立采样 `M_i ~ U(18%, 22%)`，默认固定 `seed=42`。一旦生成，`M_i` 在整个 simulation 中保持不变；Sequential 和 Adaptive 显式共用同一组 moisture realization。

## 收割能力

所有数值都是 effective in-field processing capacity，已经包含 harvesting、turning、unloading、normal field delays、combine tank cycle 和 grain-handling interaction；模拟器不会再次扣除这些效率。

| 作业/模式 | 能力 (bu/h) | 按 200 bu/ac 换算 (ac/h) |
| --- | ---: | ---: |
| Rectangular Opening, fixed 2C | 3538.46 | 17.6923 |
| Irregular Opening, fixed 2C | 2692.31 | 13.4615 |
| Interior 1C | 2000 | 10.0 |
| Interior 2C | 4000 | 20.0 |
| Interior 2C+1GC | 5000 | 25.0 |

Opening 固定采用2C，但使用独立于Interior 2C的有效能力；当前暂定值保留了旧模型规则/不规则 Opening 相对 Interior 的效率比例，之后可直接用GNSS Opening/Interior数据替换。旧的1500 bu/h保留为 `legacy_interior_rate_buph` sensitivity参数，不属于当前三个nominal action。MVP中的Interior mode切换时间默认为0分钟，但可通过 `--mode-switch-time-min` 设置。

## 运输与下游模型

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| Combine tank | 500 bu | 联合收割机粮箱容量 |
| Semi count | 1 | 半挂车数量 |
| Semi capacity | 1000 bu | 单车容量 |
| Semi unload time | 10 min | 半挂车卸入接收坑的固定时间 |
| Receiving pit | 1000 bu | 一车容量的站内接收坑 |
| Grain leg | 3000 bu/h | 从接收坑送入 wet bin 的最大速度 |
| Wet bin | 10000 bu | dryer 之前的湿粮仓容量 |
| Field moisture | U(18%, 22%) | 每个田初始化采样一次，场景内固定 |
| Dryer throughput | `D(M)=2600-100M` | 由 FIFO 队首粮食的 moisture 决定 |

半挂车完整周期是：

```text
5 min field:  5 去程 + 10 卸货 +  5 返程 = 20 min
15 min field: 15 去程 + 10 卸货 + 15 返程 = 40 min
20 min field: 20 去程 + 10 卸货 + 20 返程 = 50 min
```

粮食流向为：

```text
combine tank -> semi -> receiving pit -> grain leg -> wet bin -> dryer
```

半挂车到站后，只有接收坑能够容纳整车粮食时才能开始固定 10 分钟卸货。Grain leg 是 wet bin 的输入设备，以最高 3000 bu/h 连续搬运。从田 `F_i` 产生的每个 grain batch 都继承该田固定的 `M_i`。Wet bin 保存有序的 `(quantity, moisture, field_id)` batch 队列并严格 FIFO；dryer 永远处理队首 batch，当前速度为 `D(M_head)=2600-100M_head`。因此 18%、19%、20%、21%、22% 分别对应 800、700、600、500、400 bu/h。

Wet bin 满时，grain leg 最多只能补入 dryer 同时排出的数量；接收坑随后可能装满，半挂车会在粮站等待，田间失去可用车辆后，combine tank 满载并进入 `BLOCKED`。因此 wet bin 和 dryer 会真实地向上游产生回压，而不是无限库存。

## Sequential 与 Adaptive

Sequential 始终按 F1 → F2 → F3 → F4 → F5 → F6 → F7 → F8 → F9 → F10 作业；Opening固定2C，Interior固定使用最快的2C+1GC（5000 bu/h）。

Adaptive 只在一整块田完成后重新决策。每个候选田都预测到该田结束，估算：

- Opening 与 Interior 的生产时间和产粮量；
- 该田的“去程 + 10 分钟卸货 + 返程”运输吞吐；
- 当前可用的 combine/semi、接收坑和 wet bin 剩余空间；
- 当前 FIFO 队首粮食，以及候选田固定 moisture 对应的 dryer 能力；
- 预计运输回压、dryer 回压和该田结束后留给后续田块的库存。

预测得分最低的田先做。该计算是轻量确定性前瞻，而不是完整复制未来事件仿真；实际执行仍使用逐事件的物理模型。

进入Interior后，Adaptive从 `{1C, 2C, 2C+1GC}` 选择mode。它把未来2小时内最多10,000 bu作为相同工作量，分别估计各mode的生产时间、田间运输溢出、wet-bin/dryer溢出和forced blocking；得分接近时优先选择预测堵塞更少的mode。这形成 `predictive intentional slowdown`，而不是等wet bin满后被迫停机。

同一块田Interior内最多切换2次，因此最多3个operating segments；每个mode至少保持30分钟后才能再次切换。田块未完成时不能换田，但允许在Interior内部重新判断生产强度。整体决策结构为：

```text
Select field
→ Opening (fixed 2C)
→ Interior mode a1
→ optional mode a2
→ optional mode a3
→ select next field
```

## 粮食守恒

每个记录时刻满足：

```text
harvested grain
  = combine tank
  + grain on every semi（田间、运输中、站内等待或卸货中）
  + receiving pit
  + wet bin
  + dried grain
```

车辆到站不会直接清空成“消失”：粮食保留在车上直到 10 分钟卸货完成，然后批量进入接收坑；之后以 grain leg 和 dryer 的连续速率逐级转移。

## 默认结果含义

- `harvest_completion_time_h`：所有田块完成收割的时刻；
- `system_completion_time_h`：全部 130,000 bu 完成烘干的时刻；
- `blocked_time_h`：combine tank 满且没有可接粮半挂车造成的停机；
- `intentional_slowdown_productive_h`：Adaptive主动使用1C或2C的生产时间；
- `intentional_rate_reduction_capacity_bu`：相对全程5000 bu/h减少的名义生产能力积分，不代表粮食损失；
- `number_of_interior_mode_switches`：所有田内mode切换总数；
- `interior_mode_sequence`：每块田实际使用的mode segments；
- `interior_*_productive_h`：三个Interior mode各自的有效生产时间；
- `post_harvest_time_h`：收割完成后继续运输与烘干的时间；
- `scheduled_harvest_rest_time_h`：收割完成前累计的计划停工时间；
- `*_active_machine_h`：实际收割、换田、驾驶或卸粮的设备小时；
- `*_occupied_machine_h`：active小时加上blocked、GC等待或田间待命小时；
- `maximum_receiving_pit_inventory_bu` 和 `maximum_wet_bin_inventory_bu`：容量约束检查；
- `mass_balance_error_bu`：最终粮食守恒误差。

自动测试覆盖三种运输周期、10分钟卸货、grain leg速度、湿粮仓回压、14小时收割/10小时停工边界、停工期间下游继续运行、seed可复现且策略间共享的field moisture、FIFO连续速率切换、Opening独立2C能力、三种Interior有效能力、每田最多2次mode切换、30分钟dwell、整田约束、自适应前瞻、逐时刻粮食守恒和所有容量上限。

Machine-hours按资源数量加权：Opening、2C和2C+1GC中的1小时分别计2个C-hours，1C中的1小时计1个C-hour，2C+1GC另计1个GC-hour。`BLOCKED`不计active但计occupied；field switching按两台combine移动计active。Semi的去程/返程与卸粮计active，GC等待和收割班次内的田间待命只计occupied；计划停工后空车停在田间不计occupied。Dryer与grain leg未包含在本设备小时汇总中。
