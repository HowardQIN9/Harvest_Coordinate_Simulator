# Harvest Coordinate Simulator

离散事件收获协调模拟器：一台联合收割机、一辆半挂车、接收坑、grain leg、wet bin 和 dryer。模型同时比较固定田块顺序与带前瞻计算的 adaptive 顺序。

## 运行

在项目目录运行：

```bash
python3 run_baseline.py --policy sequential
python3 run_baseline.py --policy adaptive
python3 run_experiments.py
python3 -m unittest discover -s tests -v
```

只使用 Python 标准库。默认结果写入 `results/current/fields10_seed42_moisture18-22_wetbin10000_leg3000/`，并按 `sequential/` 和 `adaptive/` 分目录：

- `metrics.json`：汇总指标；
- `timeseries.csv`：每个时间步和事件边界的库存与状态；
- `transport_events.csv`：装车、出发、到站、开始/完成卸货和返田事件；
- `overview.svg`：完整仿真图；
- `first_3h.svg`：前三小时细节图；
- `field_scenario.csv`：所有策略共用的田块面积、形状、距离、moisture 和 dryer rate；
- `policy_comparison.csv`：两种策略的汇总对比。

旧模型产生的结果保留在 `results/archive/`，目录说明见 `results/README.md`。

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

总面积为 650 ac；按 200 bu/ac 计算，总粮量为 130,000 bu。新增的 F7–F10 中，两个为规则田、两个为不规则田，单程运输时间均为中间档 15 分钟。每块田先完成 10 ac Opening，再完成剩余面积的 InteriorHarvest。开始一块田以后，必须完成该田两个阶段才能换到下一块田；换田耗时 20 分钟。因此十块田固定产生九次换田，共 180 分钟。

每个田块的 moisture 是场景参数。在 simulation 初始化时独立采样 `M_i ~ U(18%, 22%)`，默认固定 `seed=42`。一旦生成，`M_i` 在整个 simulation 中保持不变；Sequential 和 Adaptive 显式共用同一组 moisture realization。

## 收割能力

| 作业 | 能力 (ac/h) | 按 200 bu/ac 换算 (bu/h) |
| --- | ---: | ---: |
| InteriorHarvest | 7.5000 | 1500.00 |
| Rectangular Opening | 6.6346 | 1326.92 |
| Irregular Opening | 5.0481 | 1009.62 |

Opening 速度保持独立，修改 interior 速度不会按比例重新缩放 Opening。

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

Sequential 始终按 F1 → F2 → F3 → F4 → F5 → F6 → F7 → F8 → F9 → F10 作业。

Adaptive 只在一整块田完成后重新决策。每个候选田都预测到该田结束，估算：

- Opening 与 Interior 的生产时间和产粮量；
- 该田的“去程 + 10 分钟卸货 + 返程”运输吞吐；
- 当前可用的 combine/semi、接收坑和 wet bin 剩余空间；
- 当前 FIFO 队首粮食，以及候选田固定 moisture 对应的 dryer 能力；
- 预计运输回压、dryer 回压和该田结束后留给后续田块的库存。

预测得分最低的田先做。该计算是轻量确定性前瞻，而不是完整复制未来事件仿真；实际执行仍使用逐事件的物理模型。

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
- `post_harvest_time_h`：收割完成后继续运输与烘干的时间；
- `maximum_receiving_pit_inventory_bu` 和 `maximum_wet_bin_inventory_bu`：容量约束检查；
- `mass_balance_error_bu`：最终粮食守恒误差。

自动测试覆盖三种运输周期、10 分钟卸货、grain leg 速度、湿粮仓回压、seed 可复现且策略间共享的 field moisture、FIFO 连续速率切换、整田约束、自适应前瞻、逐时刻粮食守恒和所有容量上限。
