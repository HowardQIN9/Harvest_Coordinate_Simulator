# 收获成本模型与最低成本搜索

## 结果解释与后续讨论（2026-10-12）

当前96个候选中，默认最低成本为1 combine、0 cart、1 semi采用reactive，总成本$138333.10，收完210.88h；要求200h内收完时同一车队采用threshold，总成本$140562.60，收完191.67h。72h没有可行候选。详细结果见 [FINDINGS_CN.md](results/current/cost_search/FINDINGS_CN.md)。

这些结果仍使用全年持有成本由本次任务承担的假设。全年持有成本是折旧、资本利息、保险、税费和存放费用，不是全年维修费用；维修已另按作业小时计算。这会显著偏向小车队，因此不能据此断言真实农场用一台combine慢收必然最省钱。

讨论中的下一种口径是：年度持有成本除以合理预计的全年使用小时，得到分摊的持有$/h，再加运行、维修和人工$/h。分母不应只采用本次模拟的作业时间。若研究的是已有车队的日常调度，应重点比较额外运行成本与延迟损失；若研究的是买一台还是两台，则必须保留资本成本。

天气可作业窗口、晚收损失、日期相关水分变化和自然降水带来的烘干节省，都是目前未建模的因素；它们可能改变最优车队与调度。用户目前仅要求讨论这些修改，**小时持有成本、天气和晚收损失尚未实施，也未重新实验**。现有rolling predictive仍未按美元目标重新优化。

## 找到的 Iowa State 报告

- [2026 Iowa Farm Custom Rate Survey, A3-10 / FM1698, March 2026](https://www.extension.iastate.edu/agdm/crops/pdf/a3-10.pdf)：第3页收割、烘干、运输，第5页工资和租赁参考。
- [Estimating Farm Machinery Costs, A3-29 / PM710, March 2026](https://www.extension.iastate.edu/agdm/crops/pdf/a3-29.pdf)：设备持有、资本回收、维修、燃油、润滑和人工的估算方法。
- [ISU September 2026 fuel-cost update](https://www.extension.iastate.edu/agdm/articles/johanns/JohSep26.html)：春季调查采用的柴油价不代表秋收现价。

A3-10 的参考值：corn combining $46.05/ac，grain cart $9.25/ac，continuous-flow drying $0.055/(bu·水分百分点)，5 mile 单程 truck hauling $0.135/bu，操作工资 $24.45/h。调查柴油假设 $2.89/gal。前几项为服务收费，不等于单辆车的运行成本；truck 项是运到市场，不能直接冒充农场内运输。

## 当前实现

`SimulationConfig.cost_config` 可配置成本；每次完整仿真自动在 metrics 输出 `total_cost_usd`、`cost_usd_per_ac`、`cost_usd_per_bu` 和各项明细。

目标函数为：

J = 全车队持有成本 + 各车型作业成本 + 各车型等待成本 + 烘干成本 + grain leg 处理成本 + wet bin 占用成本 + 固定设施持有成本 + 可选延迟成本。

约束：所有田块收完、所有粮食烘干、满足原物理容量和 STOP 规则；搜索可增加收获完成时限。未完成的方案不能靠少处理粮食取得低价。

设备年度持有成本按 A3-29 计算：`(购价−残值)×资本回收系数 + 利率×残值 + 0.01×(购价+残值)/2`。乘配置车数与 `campaign_ownership_share`，不随作业/等待时长重新计算。默认 share=1，含义是本次650 ac任务承担全年成本；若还有其他农场或作业，应按实际分摊比例调整。

作业小时采用现有精确 machine-hours。燃油用 A3-29 的 `0.044×hp×$/gal`，润滑=燃油的15%，人工=工资的1.1倍。等待收人工与10%燃油、润滑；不收作业维修。堵塞与模式切换按 occupied−active 计，planned STOP 释放人工。转田 combine 算 active。semi 行驶与卸粮算 active，排队与田间待命算 waiting。

所有配置车辆都收持有成本，即使模式没有启用它。现有 grain cart active-hours 只计其 productive contribution；只作为 buffer 的装卸、转田运行尚未逐车记录，不能视为完整 cart fuel 测量。车型明细是车队汇总，不是逐台设备传感器账单。

烘干按每田实际水分与默认15%目标之间的正差、实际总产量计算，使用 A3-10 continuous-flow 平均收费，不再加烘干燃油/人工。收费未给出独立资本分项，因此默认额外 dryer ownership=0，避免重复收费；如要单独计资本，应改用实测纯可变烘干成本。湿粮 bu 沿用 simulator 单位，没有额外构造 shrink 或干粮销售收入。

**所有设备购价、残值、马力、维修费、设施持有费、leg处理费、idle比例和延迟费均为示例假设，不是 ISU 报告中的设备报价。** Semi 使用柴油马力近似也需用实际 mpg、路程或油耗替换。wet-bin 占用成本默认为0，保留其可配置接口；若采用 A3-10 bin租赁 $0.030/(bu·月) 作为代理，应关闭对应自有 bin capital，避免重复计费。默认美元结果用于探索，不代表 Aaron 实际成本。

## 运行

```powershell
python run_cost_search.py --write-default-config configs/costs_illustrative.json
python run_cost_search.py --cost-config configs/costs_illustrative.json
python run_cost_search.py --cost-config configs/costs_illustrative.json --max-harvest-h 72 --output-dir results/current/cost_search_deadline72
python run_cost_search.py --cost-config configs/costs_illustrative.json --max-harvest-h 200 --output-dir results/current/cost_search_deadline200
```

编辑 JSON 即可改设备成本、柴油价、工资、目标水分、成本分摊与延迟费用。相同 seed 下所有候选使用相同田块、产量、水分和顺序。

搜索1–2 combine、0–1 cart、1–4 semi，并比较 fixed、threshold、reactive，以及所有可行的固定 Opening/Interior mode 组合。当前 rolling predictive 的原目标保持现有定义，未宣称其已按美元重新优化；这里是独立的成本排序与有限枚举优化入口。没有枚举所有逐田 mode、STOP、田块顺序、设施扩容；最低成本只指所枚举集合内的最低值。

输出 `ranking.csv`（完整排序）、`summary.json`（参数、最佳配置、完成时间、明细、deadline淘汰原因）、`best_time_series.csv`（最佳方案轨迹）。比较收获时限和设备分摊后才能判断多投入设备的经济价值；默认延迟成本为0，较慢的小车队可能最便宜。

用于研究结论前，需要替换实际车队/设施价格与维修油耗，确定 campaign 分摊、烘干计费口径、付薪等待规则、交付时限及延迟损失，并在独立场景上验证成本排序。
