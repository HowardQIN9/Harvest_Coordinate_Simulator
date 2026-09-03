# Results directory

结果按“当前模型”和“历史模型”分开保存。

## Current

`current/fields10_seed42_moisture18-22_wetbin10000_leg3000/` 是当前 10 田默认场景：

```text
policy_comparison.csv
field_scenario.csv
sequential/
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

运行 `python run_baseline.py --policy sequential` 或 `--policy adaptive` 会更新对应策略目录。运行 `python run_experiments.py` 会更新策略对比和两张完整图。

## Archive (local only)

`archive/` 是本机保留的历史输出，已由 `.gitignore` 排除，不上传 GitHub。GitHub 只保留当前正式场景，旧的受版本控制结果仍可从 Git 历史追溯。

- `archive/basic_downstream/`：最早的 D1000 简化下游模型结果。
- `archive/field_completion/`：整田完成约束开发阶段的结果。
- `archive/learning/`：早期学习实验结果。
- `archive/semi_transport/`：加入半挂车运输、但尚未采用当前 wet-bin/dryer 模型的结果。
- `archive/wetbin_dryer_two_tier/`：旧的“前 2 块 500、后 8 块 800”烘干档位结果。
- `archive/wetbin_dryer_three_tier/`：旧的“前 3 块 500、中 4 块 800、后 3 块 1000”烘干档位结果。

Archive 只用于追溯历史，不会被当前运行脚本覆盖。
