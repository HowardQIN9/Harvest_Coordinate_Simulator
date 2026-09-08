# Results directory

结果按“当前模型”和“历史模型”分开保存。

## Current

`current/fields10_seed42_moisture18-22_wetbin10000_leg3000_harvest14h_rest10h_forecast12h_restcoverage1/` 是当前10田默认场景。Opening固定使用独立2C能力，Sequential的Interior固定使用2C+1GC；Adaptive使用至少12小时的预测窗口，沿FIFO batch顺序计算dryer在10小时停工内所需的最低供粮量，并通过田块选择和1C/2C/2C+1GC模式避免预计短缺。超过最低量不会触发主动停收。Semi采用连续卸粮：满载名义速度6000 bu/h，grain leg和dryer同步运行，pit回压可延长卸粮时间，车清空后才返田：

```text
policy_comparison.csv
field_scenario.csv
sequential/
  metrics.json
  timeseries.csv
  transport_events.csv
  overview.svg
  first_3h.svg
reactive/
  metrics.json
  timeseries.csv
  transport_events.csv
  overview.svg
  first_3h.svg
adaptive/
  metrics.json
  timeseries.csv
  transport_events.csv
  overview.svg
  first_3h.svg
```

运行 `python run_baseline.py --policy sequential`、`--policy reactive` 或 `--policy adaptive` 会更新对应策略目录。运行 `python run_experiments.py` 会更新三策略对比和完整图。`--rest-shortage-weight 0`可关闭夜间保障评分；默认值1只把预计短缺按等价dryer小时加入Adaptive评分。

## 14h harvest / 10h rest

`harvest_schedule_14h_on_10h_off/` 保存从`t=0`开始、每天收割14小时后计划停工10小时的独立场景结果。计划停工期间不产生新粮，但semi、grain leg与dryer继续运行；12小时预测窗口会显示在场景目录名和`metrics.json`中。

该场景根目录中的`machine_hours_summary.csv`比较两种策略的全季C、GC和semi设备小时，`daily_machine_hours_comparison.csv`合并两种策略的逐日结果；每个策略目录中的`daily_machine_hours.csv`按24小时列出active与occupied machine-hours。

## Archive (local only)

`archive/` 是本机保留的历史输出，已由 `.gitignore` 排除，不上传 GitHub。GitHub 只保留当前正式场景，旧的受版本控制结果仍可从 Git 历史追溯。

- `archive/basic_downstream/`：最早的 D1000 简化下游模型结果。
- `archive/field_completion/`：整田完成约束开发阶段的结果。
- `archive/learning/`：早期学习实验结果。
- `archive/semi_transport/`：加入半挂车运输、但尚未采用当前 wet-bin/dryer 模型的结果。
- `archive/wetbin_dryer_two_tier/`：旧的“前 2 块 500、后 8 块 800”烘干档位结果。
- `archive/wetbin_dryer_three_tier/`：旧的“前 3 块 500、中 4 块 800、后 3 块 1000”烘干档位结果。

Archive 只用于追溯历史，不会被当前运行脚本覆盖。
