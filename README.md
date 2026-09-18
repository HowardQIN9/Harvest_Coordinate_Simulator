# Harvest Coordinate Simulator

这是一个离散事件的玉米收获—运输—烘干协调模拟器。当前探索阶段围绕一个冻结的研究问题：

> **When does predictive coordination provide additional value over current-state reactive coordination under downstream constraints?**

完整的研究边界、证据状态、策略定义和 preliminary mechanism experiment 见 [autonomous_harvest_experiment_design.md](autonomous_harvest_experiment_design.md)。当前数据支持的结论、尚未成立的主张、transition-region 解释和 receiving-pit 讨论集中记录在 [PRELIMINARY_FINDINGS.md](PRELIMINARY_FINDINGS.md)。

## 四种策略

- `fixed`：按给定田块顺序运行；Opening 和 Interior 始终使用当前 Fleet 可支持的最大 capacity；不主动 STOP。
- `threshold`：只根据当前田间可用储量压力和 wet-bin 占用压力进行分段决策。阈值当前手动设定，后续使用独立 calibration scenarios 优化。
- `reactive`：只根据 current state 的剩余 buffer、committed grain、当前 harvest/transport/dryer rate 和 response time 决策。
- `predictive`：在田块和设备信息完全可观测的确定性设定下，克隆当前 Vehicle、卸粮、wet-bin 和 dryer 状态，对 mode 与 planned STOP 组合做滚动时域预测。

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

只有已经处于当前可行的最低速模式，并且 $P\ge\theta_{\mathrm{stop}}$ 的风险信号连续保持 30 min，Threshold 才请求 planned STOP。恢复使用低于 $\theta_2$ 的 hysteresis 条件。

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

这里的 ETA rollout 只推进决策时已经存在的 committed semi trips，不生成未来收割造成的新装满、发车或下一趟运输，也不读取未来田块、yield 或 moisture。因此它属于 ETA-aware current-state reaction，而不是 Predictive 的多阶段未来模拟。如果包括 `1C` 在内的全部可行模式都不安全，风险信号连续保持 30 min 后才请求 planned STOP；STOP 满足最短时长后，至少一个可行模式重新具有非负 ETA-aware safety margin 才恢复。

### Predictive strategy 的目标定义

Predictive 使用 deterministic perfect information：当前固定顺序中所有候选田块的 area、shape、yield、moisture、Opening factor 和 travel time 均已知；Fleet、单车库存、semi event clocks、wet-bin FIFO lots 和 dryer 参数均已知。天气、故障和观测误差留到后续版本。未来库存、车辆运动、堵塞和启停不是外部直接给定，而是由候选控制计划调用与真实系统相同的物理 Simulator 向前演算得到。

必须区分三个时间尺度：

- Simulator 和 Controller 每 1 min 更新、观察一次真实状态；
- 普通 mode 至少保持 30 min，Predictive 最迟每 30 min 重新规划；planned STOP 仍受 6 h minimum duration 约束；
- 每次重规划的主动规划窗口为

$$
H_P=48\ \mathrm{h}.
$$

如果当前真实时间为 $t$，Predictive 研究 $[t,t+H_P]$ 内的 mode 与 planned-STOP 安排，但真实系统只执行所选路径的第一个动作。到下一个合法决策点后，Controller 从新的真实状态再次向前滚动 48 h。48 h 是主动规划窗口，不是单个动作持续时间，也不是整个仿真时长。

#### Forecast 与真实 Simulator

Forecast 是 Predictive 内部从当前真实状态克隆出的假设未来；real simulation 是最终产生实验结果的主仿真。两者必须调用同一套 Field、Vehicle、transport、wet-bin、dryer、mode-switch 和 STOP 物理逻辑：

$$
\text{Forecast physics}=\text{Real-simulation physics}.
$$

在相同初始状态和相同 open-loop action sequence 下，两者必须得到一致的单车库存、semi event、wet-bin、dryer、forced/planned STOP 和完成时间。每个预测 node 保存一个互相独立的完整系统状态快照，包括田块剩余量、每台 Vehicle 的载粮与状态、semi 的位置和 ETA、wet-bin FIFO lots、dryer 状态、当前 mode、minimum-dwell/mode-switch 约束以及 STOP 时钟。预测分支不得修改真实状态或其他分支。

#### 48 h 以后的真实 tail rollout

旧的乐观 terminal estimate

$$
T_{\mathrm{tail}}\approx
\frac{\text{remaining grain}}{\text{theoretical maximum harvest rate}}
$$

忽略了未来 semi 循环、wet-bin 堵塞、forced blocking、planned STOP 和后续田块转换，因此必须删除。每个保留到 48 h 边界的 leaf node 改由固定的 Threshold tail policy 接管，并继续调用完整 Simulator，直到 harvest completion 和 system completion。Tail policy 是对候选后果的真实续算，不是代数估计；它使所有 leaf 使用相同的可解释 fallback 进行公平比较。

有了完整 tail rollout，目标设计不再因 48 h 边界处于 STOP/blocking 而自动把主动规划窗口延长到 72 h。以后可把 $H_P\in\{24,48,72\}\ \mathrm{h}$ 作为独立敏感性实验，但第一版固定 $H_P=48\ \mathrm{h}$。

#### 压力引导的 Beam Search

若把 `1C / 2C / 2C+1GC / STOP` 每 30 min 完全展开，48 h 内最多形成 $4^{96}$ 条动作序列，因此目标不是穷举全局最优，而是在保证每条被评估路径物理正确的前提下，保留有希望的路径。

搜索在每个 node 上使用该 node 自己的田间可用储量压力 $P_{\mathrm{field}}$ 和 wet-bin 占用压力 $P_{\mathrm{wetbin}}$ 生成少量候选动作：低压力主要向高速模式搜索；中压力保留当前模式及相邻快/慢模式；高压力主要向 `1C` 和 `STOP` 搜索，同时保留一个相邻的较快模式，避免过早剪掉能够防止 dryer 缺粮的恢复路径。压力负责生成候选，不直接代替完整 rollout 的最终评价。

物理状态仍每分钟推进；搜索树在 mode 最短保持时间结束、压力区间改变、关键 semi 到达/离开、田块完成、全体 combine 接近 blocked、STOP 可开始或可恢复等有决策意义的事件处扩展。这里的“扩展”是从一个预测状态分别执行其合法候选动作，从而产生下一层预测状态。

每一层依次执行：

1. 删除违反 Fleet 数量、Opening/Interior mode-switch、minimum dwell、STOP 或 dryer 连续供粮约束的 node；
2. 删除在 harvested grain、meaningful forced-block time、planned-STOP 使用和库存安全性上均不优于另一 node 的被支配状态；
3. 对时间、剩余田块、$P_{\mathrm{field}}$、$P_{\mathrm{wetbin}}$、semi 位置/ETA 和 mode 足够接近的状态做近似去重，只保留表现更好的一个；
4. 若剩余状态仍过多，最多保留 `predictive_beam_width` 个 node。

`predictive_beam_width=50` 只是防止计算量失控的可调上限，不表示有 50 种动作，也不是物理参数。搜索动作仍由压力状态和合法性产生。

#### 候选排序与滚动执行

令 $\widehat T_a$ 为候选计划 $a$ 经过 48 h Predictive planning 加完整 Threshold tail 后的预计 harvest completion time，先求

$$
\widehat T_{\min}=\min_a \widehat T_a.
$$

只保留满足

$$
\widehat T_a\le \widehat T_{\min}+30\ \mathrm{min}
$$

的近最短计划；随后依次最小化 meaningful forced-block time、planned-STOP 启动次数和 system completion time。最后以较高的当前 harvest rate 和较短的等待作为 deterministic tie breakers。这样 planned STOP 不能通过无限延长停机来“消除” forced stop。真实系统只执行最佳候选的第一个动作，之后使用新的真实状态再次预测。

> **实现状态：**代码已删除 48--72 h 自动扩展和乐观 terminal estimate。当前可验证版本采用 one-step rollout：压力引导生成当前候选，候选动作保持一个30 min决策间隔，之后立即由 Threshold 规则和完整逐车物流模型续算到 harvest/system completion。因此未来启停是物理模拟结果，但主动优化前缀目前只有30 min。把主动规划前缀扩展到完整48 h仍需要可复用的多层 Beam node、支配剪枝和相似状态合并；在该状态重构完成前，`predictive_beam_width`只是当前 macro-action frontier 的候选上限，不能把当前代码称为完整多层 Beam Search。

## Version 1 物理模型契约

### Field 与作业顺序

`Field` 只保存田块自身的输入和动态剩余量：田块编号、面积、shape、yield、moisture、到 grain center 的单程时间、Opening 面积比例、Opening capacity 系数，以及 Opening/Interior 剩余面积。田块顺序不写入 `Field`，由可替换的 `FieldSelector` 选择；Version 1 使用固定顺序实现，不使用 graph 或作业 DAG。

每块田严格按 `Opening -> Interior` 完成。Opening 到 Interior 不增加额外转换时间，该影响已经包含在田间有效 capacity 中。田块之间使用统一的 `field_switch_time_h`；换田时不生产新粮，但车辆运输、卸粮和 dryer 继续推进。

当前田块属性由人工输入或带固定 random seed 的 `FieldGenerator` 生成。所有 matched policies 必须共享同一组已经生成的田块；未来可用由真实田块信息推导 shape、Opening area/fraction 和 Opening capacity factor 的模型替换 generator。

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
W(t)=T_H(t)+T_B(t)+T_M(t).
$$

`FIELD_SWITCHING`、`PLANNED_STOP` 和 `POST_HARVEST` 不计入 $W$。当 $W$ 达到可调参数 `stop_work_requirement_h` 后，strategy 才有资格提交 STOP risk signal。信号必须连续保持

$$
T_{\mathrm{confirm}}=0.5\ \mathrm{h}
$$

才开始 planned STOP，瞬时压力跳变不会直接停机。Threshold、Reactive 和 Predictive 分别用 pressure、committed semi ETA 和 48 h pressure-guided rollout 计算 risk/resume signal；共同的 STOP supervisor 只执行资格、确认时间和物理约束。Predictive 的 48 h leaf 必须通过完整 Threshold tail 续算到系统完成，不得使用乐观 terminal estimate。

每次 planned STOP 至少持续 `minimum_stop_h=6 h`，结束后 $W$ 归零。STOP 只停止新粮生产；partial semi 可发车，combine、cart 和 semi 中已有粮食继续转移，已发车 semi 继续行驶，单卸粮位继续按 FIFO 工作，dryer 继续运行。若当前田能够在出现 forced blocking 前安全完成，田块完成边界可以作为开始 STOP 的软偏好；当前版本保留该接口语义，但尚未为它设置独立 objective penalty，因此它不是 hard constraint，也不会覆盖防堵和 dryer 约束。

Dryer 从第一次实际处理粮食开始，到 system completion 之前，因缺粮造成的任一连续停机 episode 满足

$$
\max_k T_{\mathrm{dryer,idle},k}\le1\ \mathrm{h}.
$$

第一次来粮以前的 startup waiting 和全部粮食处理完成后的时间不计入该 episode。Planned STOP 启动前，STOP supervisor 会在复制的逐车物流状态上检查：至少6 h STOP 不会突破此上限；STOP 满6 h 后，如果 dryer 缺粮 episode 已达到1 h 上限，则强制恢复。Predictive 还会把恢复收割后新粮到达 wet bin 的延迟放入完整 rollout。Wet bin 不需要清空；相反，恢复必须发生在继续停止会使 dryer 长时间断粮以前。

### 时间分辨率、日志与完成条件

Simulator 和 Controller 默认每 1 min 更新一次。状态 snapshot 默认每 5 min 写一次，可改为 15 min；状态变化事件立即写入独立 event log。质量守恒和容量上限仍每分钟检查，不依赖 snapshot 频率。

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

其中 `dryer_idle_time_h` 包含第一次来粮以前的 startup waiting；另行报告的 `dryer_starvation_time_after_start_h`、`maximum_dryer_idle_episode_h` 和 `maximum_dryer_idle_episode_during_planned_stops_h` 排除 startup waiting，用于检查1 h连续停机约束。

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
| Wet bin | 10,000 bu |
| Dryer | continuous FIFO；暂用 `D(M)=2600-100M`，`M=18–22%` |
| STOP | 资格时钟累计 ≥6 h；risk signal 连续30 min；每次至少6 h |
| Dryer continuity | startup 后单次缺粮停机 ≤1 h；STOP 前做6 h feasibility guard |
| Forced STOP deadband | ≤0.1 h 的连续堵塞事件保留在raw trace，但不计入meaningful forced指标 |
| Predictive search | 压力引导候选；关键事件扩展；支配/相似状态剪枝；beam-width cap 50 |
| Simulation / control update | 每 1 min |
| Reactive mode safety horizon | 1 h；horizon 内使用当前 committed semi ETA |
| Predictive rollout（当前） | 当前动作主动规划30 min；随后使用 Threshold 和完整物流模型续算到完成；不使用 terminal estimate |
| Predictive rollout（目标） | 多层Beam主动规划48 h；leaf 使用同一 Threshold tail 续算到完成 |

STOP 是 system scheduling action，不是 field mode：只停止新粮生产，combine/cart/semi 中已有粮食仍可转运，grain leg 和 dryer 继续运行。Planned STOP 与 forced blocking 分开报告。

## 运行

在项目根目录运行：

```bash
python run_baseline.py --policy fixed
python run_baseline.py --policy threshold
python run_baseline.py --policy reactive
python run_baseline.py --policy predictive
python run_experiments.py
python run_experiments.py --joint-capacity-mechanism `
  --joint-capacity-scales 1 2 4 --joint-buffer-scales 1
python -m unittest discover -s tests -v
```

`run_baseline.py` 运行一个策略并保存完整数据：

- `metrics.json`
- `timeseries.csv`
- `transport_events.csv`
- `controller_decisions.csv`（Predictive 每次决策的全部候选、预测结果、可选性和最终选择）
- `daily_machine_hours.csv`
- `overview.svg`
- `first_3h.svg`
- scenario-level `field_scenario.csv`

`run_experiments.py` 在同一个 moisture realization 上比较四种策略，输出：

- `policy_comparison.csv`
- `machine_hours_summary.csv`
- `daily_machine_hours_comparison.csv`
- 每个策略的 `overview.svg`
- Predictive 的 `controller_decisions.csv`

结果默认写入 `results/current/` 下带完整参数的场景目录，不覆盖旧命名的结果。

## Preliminary mechanism experiment

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

## 常用覆盖参数

```bash
python run_baseline.py --policy predictive `
  --wet-bin-capacity 10000 `
  --grain-leg-rate 3000 `
  --combine-count 2 `
  --grain-cart-count 1 `
  --semi-count 3 `
  --combine-capacity 1600 `
  --grain-cart-capacity 800 `
  --seed 42 `
  --moisture-min 18 `
  --moisture-max 22 `
  --dryer-intercept 2600 `
  --dryer-slope 100 `
  --opening-fraction 0.15 `
  --mode-dwell-min 30 `
  --max-mode-switches 2 `
  --mode-switch-time-min 5 `
  --decision-interval-min 1 `
  --predictive-decision-interval-min 30 `
  --predictive-beam-width 50 `
  --prediction-horizon-h 48 `
  --work-before-stop-h 6 `
  --minimum-stop-h 6 `
  --stop-confirmation-min 30 `
  --maximum-dryer-idle-h 1 `
  --forced-block-ignore-h 0.1 `
  --harvest-tolerance-h 0.5 `
  --minimum-field-reduction-h 5 `
  --field-order-rule far_to_near `
  --name-suffix trial
```

以上是 PowerShell 的续行写法；也可以写成一行。
`--harvest-tolerance-h`直接定义 Predictive 的 near-shortest completion band；`--minimum-field-reduction-h`保留为探索性结果口径，不进入当前在线排序。

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
