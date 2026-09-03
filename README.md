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

只使用 Python 标准库。默认结果写入 `results/wetbin_dryer/`：

- `*_metrics.json`：汇总指标；
- `*_timeseries.csv`：每个时间步和事件边界的库存与状态；
- `*_transport_events.csv`：装车、出发、到站、开始/完成卸货和返田事件；
- `*_plots_20pt.svg`：完整仿真图；
- `*_first3h_20pt.svg`：前三小时细节图。

所有图使用 20pt 字体，子图标题放在左侧竖排，并为标题、坐标和图例预留独立空间。

命令行可覆盖下游参数：

```bash
python3 run_baseline.py --policy adaptive \
  --wet-bin-capacity 10000 \
  --grain-leg-rate 3000 \
  --dryer-high-rate 500 \
  --dryer-low-rate 800 \
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

总面积为 390 ac；按 200 bu/ac 计算，总粮量为 78,000 bu。每块田先完成 10 ac Opening，再完成剩余面积的 InteriorHarvest。开始一块田以后，必须完成该田两个阶段才能换到下一块田；换田耗时 20 分钟。因此六块田固定产生五次换田，共 100 分钟。

Moisture 不绑定 F1–F6：实际收割顺序中的第 1、2 块田分配 High/500 bu/h，第 3–6 块田分配 Low/800 bu/h。Adaptive 改变田块顺序时，这两个档位也随顺序重新分配。

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
| First two fields dryer | 500 bu/h | 实际收割顺序的第 1、2 块田 |
| Remaining fields dryer | 800 bu/h | 实际收割顺序的第 3–6 块田 |

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

半挂车到站后，只有接收坑能够容纳整车粮食时才能开始固定 10 分钟卸货。Grain leg 是 wet bin 的输入设备，以最高 3000 bu/h 连续搬运。每块田开始时按照它在实际收割顺序中的位置分配 500 或 800 档；粮食进入 wet bin 后仍按先入先出顺序烘干，速度由队首粮食所属的顺序档位决定。

Wet bin 满时，grain leg 最多只能补入 dryer 同时排出的数量；接收坑随后可能装满，半挂车会在粮站等待，田间失去可用车辆后，combine tank 满载并进入 `BLOCKED`。因此 wet bin 和 dryer 会真实地向上游产生回压，而不是无限库存。

## Sequential 与 Adaptive

Sequential 始终按 F1 → F2 → F3 → F4 → F5 → F6 作业。

Adaptive 只在一整块田完成后重新决策。每个候选田都预测到该田结束，估算：

- Opening 与 Interior 的生产时间和产粮量；
- 该田的“去程 + 10 分钟卸货 + 返程”运输吞吐；
- 当前可用的 combine/semi、接收坑和 wet bin 剩余空间；
- 当前队首粮食，以及本次顺序位置对应的 dryer 能力；
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
- `system_completion_time_h`：全部 78,000 bu 完成烘干的时刻；
- `blocked_time_h`：combine tank 满且没有可接粮半挂车造成的停机；
- `post_harvest_time_h`：收割完成后继续运输与烘干的时间；
- `maximum_receiving_pit_inventory_bu` 和 `maximum_wet_bin_inventory_bu`：容量约束检查；
- `mass_balance_error_bu`：最终粮食守恒误差。

自动测试覆盖三种运输周期、10 分钟卸货、grain leg 速度、湿粮仓回压、按实际田块顺序分配 dryer 档位、FIFO 切换、整田约束、自适应前瞻、逐时刻粮食守恒和所有容量上限。
