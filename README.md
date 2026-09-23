# Harvest Coordinate Simulator

这是一个离散事件的玉米收获—运输—烘干协调模拟器。当前探索阶段围绕一个冻结的研究问题：

> **When does predictive coordination provide additional value over current-state reactive coordination under downstream constraints?**

本README记录当前代码和当前验证口径。[autonomous_harvest_experiment_design.md](autonomous_harvest_experiment_design.md)与[PRELIMINARY_FINDINGS.md](PRELIMINARY_FINDINGS.md)保留早期研究设计和历史结果，其中的12 h/8 h STOP、15 min控制、48 h Beam等描述不代表当前Rolling Predictive实现。

## 四种策略

- `fixed`：按给定田块顺序运行；Opening 和 Interior 始终使用当前 Fleet 可支持的最大 capacity；不主动 STOP。
- `threshold`：只根据当前田间可用储量压力和 wet-bin 占用压力进行分段决策。阈值当前手动设定，后续使用独立 calibration scenarios 优化。
- `reactive`：只根据 current state 的剩余 buffer、committed grain、当前 harvest/transport/dryer rate 和 response time 决策。
- `predictive`：当前正式验证指`RollingFourFieldPredictive`；它克隆完整物理状态，对当前1 h mode和田界planned STOP做四田滚动预测，未来由动态Reactive tail续算。

`sequential` 和 `adaptive` 仍可作为兼容命令使用，分别映射到 `fixed` 和 `predictive`；新实验与结果应使用四个正式名称。

所有策略使用同一田块顺序。实际 planting order 尚未取得，因此默认顺序是明确标注为暂定的 far-to-near 场景，而不是声称 Aaron 实际按距离收割。

### Fixed strategy

令 $\mathcal M_O^{\mathrm{feas}}$ 和 $\mathcal M_I^{\mathrm{feas}}$ 分别表示当前 Fleet 在 Opening 和 Interior 可支持的模式集合。对田块 $f$，Fixed 选择

$$
m_{f,O}^{\mathrm{fixed}}
=\underset{m\in\mathcal M_O^{\mathrm{feas}}}{\arg\max}\;
Q_{f,m}^{\mathrm{Opening}},
$$

$$
m_{f,I}^{\mathrm{fixed}}
=\underset{m\in\mathcal M_I^{\mathrm{feas}}}{\arg\max}\;
Q_{f,m}^{\mathrm{Interior}}.
$$

选定后不主动切换模式，并且在 harvest completion 之前始终令

$$
a_{\mathrm{schedule}}^{\mathrm{fixed}}(t)=\mathrm{HARVEST}.
$$

因此默认 Fleet 下 Opening 使用 `2C`，Interior 使用 `2C+1GC`。Fixed 不进行 planned STOP；出现的停机只能来自物理系统的 forced blocking。

### Threshold strategy 的观测量

Threshold 不使用“整个系统库存压力”做决策，只使用以下两个当前状态量。

对当前模式 $m$，令 $\mathcal C_m$ 为 active combines，$\mathcal G_m$ 为 active grain carts，$\mathcal S$ 为全部 semis，$\mathcal S_F(t)$ 为时刻 $t$ 位于田间的 semis。田间潜在移动储量为

$$
C_{\mathrm{field},m}
=
\sum_{i\in\mathcal C_m}C_i
+
\sum_{j\in\mathcal G_m}C_j
+
\sum_{s\in\mathcal S}C_s.
$$

当前能够立即接收新粮的田间空间为

$$
F_{\mathrm{field},m}(t)
=
\sum_{i\in\mathcal C_m}\bigl(C_i-L_i(t)\bigr)
+
\sum_{j\in\mathcal G_m}\bigl(C_j-L_j(t)\bigr)
+
\sum_{s\in\mathcal S_F(t)}\bigl(C_s-L_s(t)\bigr).
$$

因此田间可用储量压力定义为

$$
P_{\mathrm{field},m}(t)
=1-\frac{F_{\mathrm{field},m}(t)}
{C_{\mathrm{field},m}}.
$$

正在去程、排队、卸粮或返回的 semi 当前不能接收田间粮食，因此不进入 $F_{\mathrm{field},m}(t)$，但仍属于 Fleet 的潜在容量。Wet-bin 占用压力为

$$
P_{\mathrm{wetbin}}(t)
=\frac{B_{\mathrm{wetbin}}(t)}
{C_{\mathrm{wetbin}}}.
$$

Threshold 使用的唯一标量输入为

$$
P_{\mathrm{threshold},m}(t)
=\max\left\{
P_{\mathrm{field},m}(t),
P_{\mathrm{wetbin}}(t)
\right\}.
$$

Dryer 只有处理速率，没有独立储粮容量，因此不进入以上 occupancy pressure。所有未完成 drying 的系统库存仍作为诊断 metric 和质量守恒的一部分保留，但不参与简单 Threshold 的分段判断。

Threshold 的分段阈值当前作为手动设置的参数。动作区间确定后，可把全部阈值写成向量 $\boldsymbol\theta$，在 calibration scenarios 集合 $\Omega_{\mathrm{cal}}$ 上求

$$
\boldsymbol\theta^\star
=
\underset{\boldsymbol\theta\in\Theta}{\arg\min}\;
\frac{1}{|\Omega_{\mathrm{cal}}|}
\sum_{\omega\in\Omega_{\mathrm{cal}}}
J\!\left(\pi_{\mathrm{threshold}}(\boldsymbol\theta),\omega\right).
$$

其中 $J$ 是预先冻结的比较目标，$\Theta$ 包含阈值顺序等合法性约束。最终结果只能在未参与调参的 test scenarios 集合 $\Omega_{\mathrm{test}}$ 上报告。这样得到的是针对指定场景分布和目标函数的最佳 Threshold，而不是对所有农场条件都成立的全局最佳值。

默认动作区间为

$$
m(P)=
\begin{cases}
2C+1GC,&P<\theta_1,\\
2C,&\theta_1\le P<\theta_2,\\
1C,&\theta_2\le P<\theta_{\mathrm{stop}}.
\end{cases}
$$

当 $P\ge\theta_{\mathrm{stop}}$、STOP 工作时钟已经满足、并且系统位于新田开始边界时，Threshold 请求 planned STOP。共享 STOP supervisor 会拒绝田内请求，并保证每田最多一次 planned STOP。STOP 达到最短时长后，当压力低于 $\theta_2$ 时恢复。

### Reactive strategy 的当前定义

Reactive 每 1 min 读取当前库存、当前 processing rates，以及通过对讲机可知的逐辆 semi 状态和 ETA。默认 mode safety horizon 为

$$
H_R=1\ \mathrm{h}.
$$

对当前已经在途、排队、卸粮或返回的 semi $s$，令 $\eta_s$ 为其预计返回田间的相对时间，$R_s$ 为返回时能够提供的空余容量。对于候选模式 $m$，在 horizon 内时刻 $u$ 的田间剩余接收空间为

$$
F_{m}^{R}(u)
=F_{\mathrm{field},m}(t)
+
\sum_{s:\eta_s<u}R_s
-Q_m u.
$$

田间安全余量检查所有已知 ETA 事件发生前的时刻以及 horizon 终点：

$$
M_{\mathrm{field},m}^{R}
=
\min_{u\in\{0,\eta_1,\ldots,\eta_n,H_R\}}
F_m^R(u).
$$

令 $F_{\mathrm{system}}(t)$ 为全部 Vehicle 与 wet bin 的当前剩余系统容量，$Q_D(t)$ 为当前 dryer rate，则系统安全余量为

$$
M_{\mathrm{system},m}^{R}
=F_{\mathrm{system}}(t)
-\max\{0,Q_m-Q_D(t)\}H_R.
$$

最终安全余量为

$$
M_m^R
=\min\left\{
M_{\mathrm{field},m}^{R},
M_{\mathrm{system},m}^{R}
\right\}.
$$

Reactive 选择满足 $M_m^R\ge0$ 的最快可行模式；如果没有模式安全，则先选择最慢可行模式。只有 STOP 资格满足后，才允许进一步选择 planned STOP。

这里的 ETA rollout 只推进决策时已经存在的 committed semi trips，不生成未来收割造成的新装满、发车或下一趟运输，也不读取未来田块、yield 或 moisture。因此它属于 ETA-aware current-state reaction，而不是 Predictive 的多阶段未来模拟。如果在新田块边界包括 `1C` 在内的全部可行模式都不安全，Reactive 请求 planned STOP；田内不允许新建 planned STOP。STOP 满足最短时长后，至少一个可行模式重新具有非负 ETA-aware safety margin 才恢复。

### Predictive strategy 的当前定义

当前正式验证使用 `harvest_sim/rolling_predictive.py` 中的 `RollingFourFieldPredictive`。它与 `Simulator(..., policy="predictive")` 中保留的旧 one-step `PredictiveController` 不同；后者仅用于兼容历史实验，不应与当前 rolling 结果混在一起。

所有对外结果统一使用策略名`policy="predictive"`。具体实现另存于`controller_implementation`：当前正式实现为`rolling_four_field_dynamic_tail`，旧兼容实现为`legacy_one_step`。因此分析脚本应先按`policy`比较策略，再用`controller_implementation`检查结果是否来自同一代控制器。

Rolling Predictive 使用 deterministic perfect information：当前固定顺序中田块的 area、shape、yield、moisture、Opening factor 和 travel time 均已知；Fleet、单车库存、semi event clocks、wet-bin FIFO lots 和 dryer 参数均已知。`forecast_yield_multiplier`可在验证实验中只修改预测副本里的未完成田块产量，真实仿真仍使用原始产量。

#### 滚动预测与动态 tail

每次决策从完整 `SimulationCheckpoint` 克隆未来状态，使用与真实系统相同的 Field、Vehicle、transport、wet-bin、dryer、mode-switch 和 STOP 物理逻辑：

$$
\text{Forecast physics}=\text{Real-simulation physics}.
$$

当前时间尺度和搜索范围为：

- 真实执行使用 1 min 物理步长；forecast 默认使用 5 min 物理步长；
- 每次只提交当前 1 h root action；若先到田界，则在田界立即重新规划；
- 每次评估当前田和后续三块未完成田，共最多四田；
- 普通 Interior mode 至少保持 30 min，每田最多切换两次；
- tail 每 1 h或到达田界重新选择最快的 current-state-safe mode，并在后续合法田界再次允许 STOP；
- 预测推进到本次四田窗口全部完成，不使用乐观 terminal estimate。

因此，当前实现不是48 h多层Beam Search。`prediction_horizon_h`和`predictive_beam_width`仍由旧控制器使用，但不决定 `RollingFourFieldPredictive` 的四田动态-tail搜索。

#### Root action 与 STOP 搜索

Root action 枚举当前阶段合法的 `1C / 2C / 2C+1GC`。只有在新田开始、STOP工作时钟满足且本田尚未使用过planned STOP时，才额外比较带STOP的root action。默认STOP搜索为：

1. 从6 h开始，每4 h粗搜到30 h；
2. 在粗搜优胜点两侧用1 h步长细化；
3. 再用0.25 h步长细化。

共享 STOP supervisor 对所有策略统一执行“仅田界启动、每田最多一次、最短6 h、dryer连续缺粮不超过系统上限”的约束。当前 rolling/validation runners把STOP资格设为累计1 h实际收获；`SimulationConfig`通用默认值仍为6 h。

#### 动态完成时间容差与候选排序

令最快候选的四田预测完成时间和raw forced blocking分别为 $T_\star$ 与 $B_\star$。当前容差为

$$
r_\star=\frac{B_\star}{T_\star},\qquad
\alpha=\operatorname{clip}(0.01+0.30r_\star,0.01,0.04).
$$

先保留满足

$$
T_a\le T_\star(1+\alpha)
$$

的候选，再依次最小化：raw forced blocking、planned STOP期间dryer idle、planned STOP总时长、STOP次数、mode switches、预测完成时间和期末未处理库存。真实系统只执行最佳候选的第一个root action，之后从新的真实状态重新预测。

这意味着1%--4%是每次四田局部预测的容差，不是对全季完工时间的硬保证。当前控制器会在局部容差允许时，用planned STOP换取更少的unplanned blocking。

#### Field buffer、dryer idle 与95% wet-bin reserve

`use_field_buffer=True`和`maximum_dryer_idle_episode_h=2`属于共享`SimulationConfig`，不是Predictive特权。启用field buffer后，全部`N_graincart`即使不贡献当前mode的收获速度，也可作为田间临时储粮；这也是以后grain bag接口的基础。

`wet_bin_control_limit_fraction=0.95`不会把物理wet bin从10,000 bu改成9,500 bu，而是在Reactive及其Rolling tail的安全余量中预留500 bu。当前验证套件在field-configuration、downstream-capacity和forecast-error三类实验中统一使用该95%控制上限；旧结果若使用1.0，必须重跑后才能与新口径合并分析。

使用以下命令运行当前rolling版本：

```bash
python run_rolling_predictive.py --verbose
```

默认10田完整运行约需数分钟到十余分钟，取决于场景和候选数量；可用`--output-dir`避免覆盖已有结果。

## Version 1 物理模型契约

### Field 与作业顺序

`Field` 只保存田块自身的输入和动态剩余量：田块编号、面积、shape、yield、moisture、到 grain center 的单程时间、Opening 面积比例、Opening capacity 系数，以及 Opening/Interior 剩余面积。田块顺序不写入 `Field`，由可替换的 `FieldSelector` 选择；Version 1 使用固定顺序实现，不使用 graph 或作业 DAG。

每块田严格按 `Opening -> Interior` 完成。Opening 到 Interior 不增加额外转换时间，该影响已经包含在田间有效 capacity 中。田块之间使用统一的 `field_switch_time_h`；换田时不生产新粮，但车辆运输、卸粮和 dryer 继续推进。

当前田块属性可由人工输入、基础 `FieldGenerator`，或带固定seed的完整 `FieldScenarioGenerator` 生成。后者同时随机化面积、亩产、shape、单程运输时间和moisture，并把每套场景归一化到650 ac和130,000 bu。所有matched policies必须共享同一组已经生成的田块；未来可用真实田块数据替换synthetic generator。

### Vehicle、Fleet 与 OperatingMode

每台移动设备都是独立 `Vehicle`，至少保存单位生产贡献、单位临时储粮容量、当前载粮和当前状态。`Fleet` 保存各类 Vehicle 的数量：

$$
N_C=\text{number of combines},\qquad
N_G=\text{number of grain carts},\qquad
N_S=\text{number of semis}.
$$

`OperatingMode` 保存当前作业实际使用的设备数量 $(n_C,n_G)$。模式只有在

$$
n_C\le N_C,\qquad n_G\le N_G
$$

时可选；Controller 返回不可行模式属于错误，不允许 Simulator 静默替换。

令单台 combine 和 grain cart 对田间有效 throughput 的贡献分别为 $q_C$ 和 $q_G$，则模式的 Interior capacity 为

$$
Q_m=n_Cq_C+n_Gq_G.
$$

当前 $q_C=1600\ \mathrm{bu/h}$，$q_G=800\ \mathrm{bu/h}$，因此 `1C / 2C / 2C+1GC` 分别为 `1600 / 3200 / 4000 bu/h`。Grain cart 的 $q_G$ 是对整个 combine team 的共享支持贡献，不表示 grain cart 独立收割。

对田块 $f$，令 $\alpha_f$ 为 Opening capacity factor，则

$$
Q_{f,m}^{\mathrm{Opening}}=\alpha_f Q_m,\qquad
Q_{f,m}^{\mathrm{Interior}}=Q_m.
$$

这些 capacity 已经包含收割、掉头和田间卸粮的综合影响。Version 1 中 combine、grain cart 和 semi 之间的田间转移不再重复增加时间。

Opening 开始时可选择 `1C` 或 `2C`，期间不切换。Interior 可在约束下切换，默认每田最多 2 次；`maximum switches`、minimum dwell 和 switching time 都是可调参数。

### 临时储粮与物流

每台 Vehicle 分别保存载粮，不用一个聚合库存替代单车状态；系统级库存只通过求和派生。对 Vehicle 集合 $\mathcal V$，时刻 $t$ 的移动库存为

$$
L_{\mathrm{mobile}}(t)=\sum_{v\in\mathcal V}L_v(t),
\qquad 0\le L_v(t)\le C_v^{\mathrm{storage}}.
$$

正常田间转移顺序是：grain cart 中已有粮食优先装入可用 semi；combine 优先直接装入可用 semi；semi 没有空间时，当前模式使用的 grain cart 才作为共享临时 buffer 接收 combine 粮食。Combine 和 grain cart 均无可用空间时，相应 combine 才会停机。只有所有当前参与作业的 combine 都无法继续时，系统状态才记为 `FORCED_BLOCKED`；部分 combine 暂停时系统仍记为 `HARVESTING`，实际 capacity 只由仍工作的设备贡献。

系统状态与设备 machine-hours 分开记账。若某一步模式中有 $n_C$ 台 active combines，其中 $w_C$ 台仍能工作，则该步增加

$$
\Delta MH_{C,\mathrm{productive}}=w_C\Delta t,\qquad
\Delta MH_{C,\mathrm{blocked}}=(n_C-w_C)\Delta t.
$$

因此一台 combine 堵塞、另一台继续工作时，系统 `FORCED_BLOCKED` time 不增加，但被堵设备的 blocked machine-hours 会增加；只有 $w_C=0$ 时系统 `FORCED_BLOCKED` time 才增加。

正常收获期间 semi 满载才离田。Planned STOP 开始时以及所有田块收获完成时，允许有载粮的 partial semi 发车。每块田保存一个到 grain center 的单程时间；Version 1 假设去程与返回相同，且 semi 出发后保留该趟 route，不受收获队随后换田影响。

Grain center 当前只有一个卸粮位，采用先到先卸。`receiving_pit_capacity_bu = 0` 表示没有 receiving pit；字段接口保留，但 Version 1 会拒绝非零值，直到 pit 的到达、暂存和出料逻辑真正实现。没有 pit 时，semi 不能绕过 wet bin 直接向 dryer 卸粮。Wet bin 满后，semi 只能按 dryer 腾出空间的速率继续卸粮，因此 wet-bin inventory 不会越界。

Wet bin 内 grain lots 保留来源田块和 moisture，并严格 FIFO。Dryer 连续运行，其瞬时 capacity 由 FIFO 队首 lot 的 moisture 决定。

### 时间分解与 STOP 资格

田间团队在每个 1-min simulation step 中只能属于一个主要状态：`HARVESTING`、`FORCED_BLOCKED`、`MODE_SWITCHING`、`FIELD_SWITCHING`、`PLANNED_STOP` 或 `POST_HARVEST`。令这些状态在 harvest completion 之前的累计时间分别为 $T_H,T_B,T_M,T_F,T_P$，则必须满足

$$
T_{\mathrm{harvest\ completion}}=T_H+T_B+T_M+T_F+T_P.
$$

令 $W(t)$ 为上次 planned STOP 结束后累计的 STOP 资格时钟，则

$$
W(t)=T_H(t).
$$

Forced blocking、mode switching、`FIELD_SWITCHING`、`PLANNED_STOP` 和 `POST_HARVEST` 均不计入 $W$。当 $W$ 达到可调参数 `stop_work_requirement_h` 后，strategy 才有资格在下一个田块边界提交一次 STOP 请求。共同的 STOP supervisor 强制执行“只在新田开始前STOP、每个田最多一次”、最短时长和dryer连续性约束；一旦开始收获该田，本田不再接受planned STOP。Threshold、Reactive 和 Predictive 分别用 pressure、committed semi ETA 和未来物理rollout决定在这个边界是否停机。

每次 planned STOP 至少持续 `minimum_stop_h=6 h`，同一次STOP可以继续延长，但恢复收获后不能在同一田再次STOP。STOP结束后 $W$ 归零。STOP 只停止新粮生产；partial semi 可发车，combine、cart 和 semi 中已有粮食继续转移，已发车 semi 继续行驶，单卸粮位继续按 FIFO 工作，dryer 继续运行。

Dryer 从第一次实际处理粮食开始，到 system completion 之前，因缺粮造成的任一连续停机 episode 满足

$$
\max_k T_{\mathrm{dryer,idle},k}\le2\ \mathrm{h}.
$$

第一次来粮以前的 startup waiting 和全部粮食处理完成后的时间不计入该 episode。Planned STOP 启动前，STOP supervisor 会在复制的逐车物流状态上检查：至少6 h STOP 不会突破此上限；STOP 满6 h 后，如果dryer缺粮episode已达到2 h上限，则强制恢复。Predictive还会把恢复收割后新粮到达wet bin的延迟放入完整rollout。Wet bin不需要清空；相反，恢复必须发生在继续停止会使dryer长时间断粮以前。

### 时间分辨率、日志与完成条件

Simulator默认每1 min推进物理状态，Fixed/Threshold/Reactive按系统control interval读取状态；Rolling Predictive每1 h或田界重新选择root action，其内部forecast默认用5 min步长。状态snapshot默认每5 min写一次，可改为15 min；质量守恒和容量上限仍按实际物理步长检查。

`harvest completion` 是所有 Field 的 Opening 和 Interior 完成的时间。此后 partial semi 全部发车，下游继续运行。`system completion` 是所有 Vehicle、receiving pit 和 wet bin 均为空且全部粮食完成 dryer 处理的时间。

### 守恒式与核心 metrics

令 $H_k$ 为第 $k$ 个时间步末的累计收获量，$L_{v,k}$ 为 Vehicle $v$ 的载粮，$P_k$ 为 receiving-pit inventory，$B_k$ 为 wet-bin inventory，$D_k$ 为累计 dried grain，则每分钟必须满足

$$
E_{\mathrm{mass},k}
=H_k-\left(\sum_{v\in\mathcal V}L_{v,k}+P_k+B_k+D_k\right)=0.
$$

对任一离散系统状态 $X$，累计时间定义为

$$
T_X=\sum_{k=0}^{K-1}\Delta t_k\,\mathbf 1(S_k=X).
$$

Wet-bin 平均库存使用 system-completion horizon 上的时间加权平均：

$$
\overline B=\frac{1}{T_{\mathrm{system\ completion}}}
\sum_{k=0}^{K_S-1}B_k\Delta t_k.
$$

Version 1 核心输出为：harvest/system completion time；productive、forced-blocked、planned-STOP、mode-switching 和 field-switching time；相应事件次数；total harvested/dried grain；mass-balance error；wet-bin maximum/average inventory；dryer operating/idle time；semi trips 和 semi queue waiting time。每次 forced-block episode 的原始持续时间保留在 event data 中，不在基础日志中删除短事件。暂不输出逐台 Vehicle 的汇总 metrics。

其中 semi 排队等待时间按所有 semi 的等待时间求和，不做车辆平均：

$$
T_{S,\mathrm{queue}}
=\sum_{k=0}^{K_S-1}\Delta t_k
\sum_{s=1}^{N_S}\mathbf 1(S_{s,k}=\mathrm{WAITING\_UNLOAD}).
$$

Dryer 时间在 system-completion horizon 上满足

$$
T_{\mathrm{dryer,idle}}
=T_{\mathrm{system\ completion}}-T_{\mathrm{dryer,operating}}.
$$

其中 `dryer_idle_time_h` 包含第一次来粮以前的 startup waiting；另行报告的 `dryer_starvation_time_after_start_h`、`maximum_dryer_idle_episode_h` 和 `maximum_dryer_idle_episode_during_planned_stops_h` 排除startup waiting，用于检查2 h连续停机约束。

## 当前基线

| 项目 | 值 |
|---|---:|
| Field area | 10 fields, 650 ac total |
| Opening | 每田 15%，可选 1C 或 2C |
| Interior rates | 1C 1,600；2C 3,200；2C+1GC 4,000 bu/h |
| Interior mode switch | 每田默认最多 2 次；30 min dwell；5 min penalty（均可调） |
| Combine tank | 300 bu/combine |
| Grain cart | 1 × 1,000 bu；单车状态独立保存（暂定） |
| Semis | 3 × 1,000 bu |
| Receiving pit | 无 |
| Grain leg | 3,000 bu/h；满载 semi 名义卸粮 20 min |
| Wet bin | 10,000 bu物理容量；通用control reserve默认0，capacity suite使用500 bu reserve |
| Dryer | continuous FIFO；暂用 `D(M)=2600-100M`，`M=18–22%` |
| STOP | 资格时钟只累计实际收获；通用默认6 h，当前rolling/validation实验使用1 h；仅新田边界可启动；每田最多1次；每次至少6 h |
| Dryer continuity | startup 后单次缺粮停机 ≤2 h；所有策略读取同一系统上限 |
| Forced STOP deadband | ≤0.1 h 的连续堵塞事件保留在raw trace，但不计入meaningful forced指标 |
| Predictive search（当前） | 当前+后续三田；1 h root action；动态Reactive tail；STOP粗搜+双向细化 |
| Simulation / control update | 真实物理1 min；rolling决策1 h或田界；forecast物理步长5 min |
| Reactive mode safety horizon | 1 h；horizon 内使用当前 committed semi ETA |
| Predictive rollout（当前） | root action后由fastest-safe Reactive tail推进到四田窗口完成；不使用terminal estimate |

STOP 是 system scheduling action，不是 field mode：只停止新粮生产，combine/cart/semi 中已有粮食仍可转运，grain leg 和 dryer 继续运行。Planned STOP 与 forced blocking 分开报告。

## 运行

在项目根目录运行：

```bash
python run_baseline.py --policy fixed
python run_baseline.py --policy threshold
python run_baseline.py --policy reactive
python run_rolling_predictive.py --verbose
python run_validation_suite.py --phase all --skip-existing
python -m unittest discover -s tests -v
```

`run_baseline.py --policy predictive`仍可运行旧one-step `PredictiveController`，但当前正式rolling结果应使用`run_rolling_predictive.py`或`run_validation_suite.py`。`run_experiments.py`保留用于复现早期mechanism experiments，不作为当前验证套件入口。

`run_baseline.py` 运行一个策略并保存完整数据：

- `metrics.json`
- `timeseries.csv`
- `transport_events.csv`
- `controller_decisions.csv`（仅旧Predictive controller）
- `daily_machine_hours.csv`
- `overview.svg`
- `first_3h.svg`
- scenario-level `field_scenario.csv`

`run_rolling_predictive.py`保存`metrics.json`、完整`timeseries.csv`、`field_scenario.csv`和当前rolling版本的`controller_decisions.csv`。后者逐次记录候选数量、局部最快时间、动态alpha、STOP/no-STOP预测以及最终root action。

验证套件的`scenario_metadata.json`还固定记录wet-bin物理容量、控制上限比例与reserve，以及Rolling Predictive的实现ID、1 h决策间隔、四田horizon、5 min forecast步长、alpha范围和三级STOP搜索参数。单次运行的`metrics.json`使用统一的`policy`名称，并用`controller_implementation`区分当前rolling实现与legacy one-step实现。

历史`run_experiments.py`在同一个moisture realization上比较四种旧版策略，输出：

- `policy_comparison.csv`
- `machine_hours_summary.csv`
- `daily_machine_hours_comparison.csv`
- 每个策略的 `overview.svg`
- Predictive 的 `controller_decisions.csv`

结果默认写入 `results/current/` 下带完整参数的场景目录，不覆盖旧命名的结果。

## FieldScenarioGenerator 与验证套件

`FieldScenarioGenerator` 用 seed 生成完整田块场景，而不再只改变湿度。它会随机化每块田的面积、亩产、形状、单程转场时间和湿度，同时把每套场景归一化到相同的 650 ac 和 130,000 bu，便于公平比较。

```bash
python run_validation_suite.py --phase generate
python run_validation_suite.py --phase field --skip-existing
python run_validation_suite.py --phase pressure --skip-existing
python run_validation_suite.py --phase error --skip-existing
```

- `field`：在S1/S2/S3上分别运行Fixed、Threshold、Reactive和当前Rolling Predictive。三套场景保持相同总面积和总粮量，但田块面积、亩产、shape、距离和moisture不同。
- `pressure`（或 `capacity`）：固定S2，配对运行四种策略，把dryer curve、grain-leg rate和名义semi卸粮能力同步放大`1 / 1.2 / 1.5 / 1.8 / 2 / 2.5 / 3×`。倍数越大，downstream越强、压力越小。该套件保持10,000-bu物理wet bin，并在Reactive/Predictive安全余量中保留500 bu；Threshold仍使用自身95% occupancy STOP阈值，Fixed不读取状态安全余量。
- `error`：固定真实S2，只令Rolling Predictive对未完成田块的预测产量产生`-5% / +5%`系统偏差；真实收获量不变。当前error suite尚未启用capacity suite的500-bu reserve。

完整轨迹写入 `results/validation/`。紧凑汇总写入 `results/validation/summary/` 下的 `scenarios.csv`、`field_configurations.csv`、`downstream_capacity.csv` 和 `forecast_error.csv`。

Forecast-error套件会独立运行`-5% / 0% / +5%`三组预测产量误差，分别写入`yield_m5pct`、`yield_0pct`和`yield_p5pct`。其中0%基线不再借用field-configuration套件的S2结果，从而保证三组使用完全相同的实验配置与输出流程。

### 当前验证结果

S1/S2/S3的Reactive与Rolling Predictive结果为：

| Scenario | Reactive harvest (h) | Predictive harvest (h) | Reactive raw block (h) | Predictive raw block (h) | Predictive planned STOP (h) |
|---|---:|---:|---:|---:|---:|
| S1 | 202.00 | 202.16 | 78.76 | 0.54 | 141.07 |
| S2 | 211.14 | 211.67 | 88.53 | 1.00 | 156.32 |
| S3 | 240.49 | 238.42 | 113.68 | 1.88 | 176.79 |

三套场景均显示：Predictive把大量constraint-induced forced blocking转化为田界planned STOP和更集中的高速收获，但没有稳定缩短system completion。当前证据支持“提高运行可控性”，不支持“普遍提高物理吞吐能力”。

S2 downstream-capacity sweep的主比较为：

| Scale | $\rho=Q_{\max}/Q_{\mathrm{downstream}}$ | Predictive − Reactive harvest (h) | Reactive − Predictive raw block (h) | Predictive STOP (h) |
|---:|---:|---:|---:|---:|
| 1.0 | 7.06 | +0.54 | 87.53 | 156.32 |
| 1.2 | 5.88 | +2.82 | 55.14 | 115.04 |
| 1.5 | 4.71 | +1.25 | 33.66 | 86.79 |
| 1.8 | 3.92 | 0.00 | 26.23 | 62.82 |
| 2.0 | 3.53 | −0.17 | 21.52 | 49.81 |
| 2.5 | 2.82 | +1.01 | 7.68 | 33.54 |
| 3.0 | 2.35 | −0.36 | 5.44 | 18.29 |

`+`表示Predictive收获更慢，`−`表示更快。当前1--3×全部满足$\rho>2$，因此仍是binding downstream conditions；真正接近nonbinding需要约7×，尚未用当前版本验证。

2×和3×下Predictive确实更多使用快速田间动作。2×时其Interior `2C+1GC` productive time为10.16 h，Reactive仅0.56 h；3×时分别为11.74和2.16 h。Predictive的田间作业时间分别比Reactive短43.97和18.65 h，但额外planned STOP分别为43.81和18.29 h，所以最终harvest completion只快0.17和0.36 h。逐分钟堵塞分类还表明，Predictive在2×/3×下因田间无semi造成的blocking仅0.31/0.07 h；主要长期上限仍是wet bin与dryer，而不是transportation。

Forecast-yield误差的当前S2结果为：`-5% / 0 / +5%`对应raw blocking `1.99 / 1.00 / 6.81 h`。+5%误差会让预测认为田内仍有更长时间供dryer排空，从而缩短部分田界STOP；真实田块更早结束后，下一个高速田块更容易遇到高库存。该结果说明误差响应具有方向性，但在统一95% reserve后仍需重跑确认。

## 历史 preliminary mechanism experiment

本节用于复现旧dryer/buffer机制实验。它使用早期Predictive controller及不同STOP/决策口径，不能与当前rolling validation直接合并。

```bash
python run_experiments.py --mechanism
```

这不是 full sweep。脚本运行：

- dryer curve scale `1× / 4× / 8×`；
- wet-bin capacity scale `0.5× / 1× / 2×`；
- 在中间 capacity/buffer 条件下附加 `4 / 6 / 8 h` work-before-STOP 检查。

共 11 个独立 scenario configurations，每个都配对运行四策略。汇总写入 `results/current/mechanism/mechanism_summary.csv`，包含 `Q_harvest / mean dryer rate`，用于寻找 weak → transition → severe constraint 区域。

若要同步放大dryer、grain leg和名义直接卸粮能力，并保留所有完整轨迹：

```bash
python run_experiments.py --joint-capacity-mechanism
```

该实验独立写入 `results/current/joint_capacity_mechanism/`，不会覆盖上述
dryer-only结果。每个策略保存metrics、timeseries、transport events、daily
machine-hours和overview图；`RUN_RECORD.md`记录精确参数、结果和限制。

## 当前 Rolling Predictive 常用参数

```bash
python run_rolling_predictive.py `
  --decision-interval-h 1 `
  --forecast-step-min 5 `
  --horizon-fields 4 `
  --completion-tolerance-min-pct 1 `
  --completion-tolerance-max-pct 4 `
  --completion-tolerance-pressure-scale 0.30 `
  --work-before-stop-h 1 `
  --maximum-stop-h 30 `
  --stop-coarse-step-h 4 `
  --stop-refine-step-h 1 `
  --stop-fine-step-h 0.25 `
  --maximum-dryer-idle-h 2 `
  --use-field-buffer `
  --output-dir results/current/rolling_dynamic_predictive
```

以上是PowerShell续行写法。`--stop-durations-h`可传入逗号分隔的固定候选；默认`auto`使用粗到细搜索。物理系统参数仍集中在`SimulationConfig`；validation suite通过不可变config副本构造配对实验。

## 物理流和守恒

```text
independent combine tanks -> semis (first choice)
                          -> active independent grain carts (temporary buffer)
independent grain carts  -> semis
semis -> single FIFO unload slot -> wet bin -> FIFO dryer
```

每个记录时刻检查：

```text
harvested grain
  = combine + grain cart + all semis + wet bin + dried grain
```

无 receiving pit。湿粮仓满时，semi 卸粮会受 dryer 出流限制；semi 清空后才返田。田块距离同时影响真实往返事件与 Predictive 克隆出的逐车未来事件。

## 结果解释

优先看：

- `harvest_completion_time_h`
- `forced_blocking_time_h`
- `raw_forced_blocking_time_h`
- `planned_stop_time_h`
- `maximum_dryer_idle_episode_during_planned_stops_h`
- `maximum_dryer_idle_episode_h`
- `field_engaged_calendar_time_h`（harvest completion 减 planned STOP）
- `system_completion_time_h`
- 设备 active/occupied machine-hours
- inventory、queue、mode 和 STOP 的时间轨迹

不要把计划 STOP 自动解释成损失，也不要只用一个百分比宣称 Predictive 更好。当前阶段的目标是找出 action timing 如何改变库存和 forced blocking，以及这种差异在哪个 constraint regime 出现。

## 当前未确认边界

- modeled fields 的真实 planting order；
- dryer 是否会在一天中改变设置、变化响应时间和适用吞吐曲线；
- 15% Opening、单台 grain cart 1,000-bu storage、30-min dwell 和 5-min switch penalty 的依据；
- soybean、weather、mud、commercial elevator 和 equipment failure 均未进入 version 1。

在上述问题完成追问和文献检查前，机制实验只应称为 preliminary/exploratory results。
