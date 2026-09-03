# Predictive Coordination for Autonomous Harvest Logistics

## 1. Core Idea

The research focus is not simply on making individual agricultural machines autonomous, but on adding a higher-level intelligence layer that coordinates the entire harvest logistics system.

The key system is:

\[
Combine
\rightarrow
Grain\ Cart
\rightarrow
Semi
\rightarrow
Storage / Dryer / Elevator
\]

The main question is:

> **How can an autonomous harvest system anticipate emerging bottlenecks and dynamically adapt upstream fleet coordination according to field conditions, fleet states, and downstream logistics constraints?**

The central logic is:

\[
\boxed{
Field/Fleet\ Context
\rightarrow
System\ Prediction
\rightarrow
Bottleneck\ Detection
\rightarrow
Upstream\ Coordination
}
\]

This shifts the research focus from **reacting to current problems** to **anticipating future system states and acting before bottlenecks actually occur**.

---

## 2. Why Predictive Coordination Matters

A harvest system is strongly coupled through material flow and finite buffers.

For example:

\[
Dryer\ congestion
\]

may cause:

\[
Semi\ turnaround\ time\uparrow
\]

which leads to:

\[
Available\ semis\downarrow
\]

then:

\[
Cart\ unloading\ opportunities\downarrow
\]

then:

\[
Cart\ load\uparrow
\]

and eventually:

\[
Combine\ idle\ time\uparrow
\]

A purely local system may only react when the problem has already propagated upstream.

A predictive system instead asks:

> **If the current state continues, where will the bottleneck be 10–30 minutes from now?**

and then changes the current coordination decision before the bottleneck becomes critical.

---

## 3. Fixed vs Reactive vs Predictive Coordination

### 3.1 Fixed Coordination

A fixed policy does not change its operating logic according to system conditions.

Examples:

- nearest cart serves the combine;
- highest-fill combine has priority;
- cart unloads only when full;
- always minimize harvest completion time.

Conceptually:

\[
a_t=\pi_{\text{fixed}}(s_t)
\]

The same policy is applied under different farm configurations and operating regimes.

### 3.2 Reactive Coordination

A reactive system observes an existing problem and then replans.

```text
Dryer becomes congested
        ↓
Congestion is observed
        ↓
Semi return is delayed
        ↓
Fleet schedule is recalculated
```

Formally:

\[
s_t
\rightarrow
\text{detect current bottleneck}
\rightarrow
a_t
\]

This is dynamic, but still reactive.

### 3.3 Predictive Coordination

A predictive coordinator estimates future states before the disturbance fully propagates.

```text
Current system state
        ↓
Predict future system evolution
        ↓
Predict emerging bottleneck
        ↓
Adapt fleet coordination now
        ↓
Avoid or reduce future congestion
```

Formally:

\[
s_t
\rightarrow
\hat{s}_{t+1:t+H}
\rightarrow
\hat{b}_{t+H}
\rightarrow
a_t
\]

where:

- \(s_t\): current system state;
- \(\hat{s}_{t+1:t+H}\): predicted future states;
- \(\hat{b}_{t+H}\): predicted future bottleneck;
- \(a_t\): current fleet coordination decision.

The main difference is:

\[
\boxed{
Reactive:
problem\ occurs
\rightarrow
respond
}
\]

versus:

\[
\boxed{
Predictive:
problem\ is\ anticipated
\rightarrow
act\ beforehand
}
\]

---

## 4. Prediction Target

Prediction should not only estimate individual variables such as:

- semi ETA;
- combine fill time;
- dryer queue;
- yield.

The more important goal is to predict the **future operating regime or bottleneck location**.

For example:

\[
b_{t+H}
\in
\{
Combine,
Cart,
Transport,
Dryer
\}
\]

The predictive model asks:

> Which subsystem is likely to become the marginal constraint on system performance?

Possible operating regimes include:

\[
R_t
\in
\{
harvest\text{-}limited,
cart\text{-}limited,
transport\text{-}limited,
downstream\text{-}limited,
urgent
\}
\]

The system can then select or adjust the appropriate coordination strategy.

---

## 5. Farm Context

Prediction and coordination should depend on the farm configuration rather than assuming one universal harvest system.

Define static or slowly changing context:

\[
c_F
\]

including:

```text
Farm Context
├── Field
│   ├── size
│   ├── geometry
│   ├── headland
│   ├── entrances
│   └── harvesting constraints
│
├── Crop
│   ├── crop type
│   ├── yield characteristics
│   └── header constraints
│
├── Fleet
│   ├── combine number/capacity
│   ├── grain cart number/capacity
│   └── semi number/capacity
│
├── Transportation
│   ├── haul distance
│   ├── road speed
│   └── travel-time uncertainty
│
└── Downstream
    ├── storage capacity
    ├── dryer capacity
    ├── receiving rate
    └── elevator constraints
```

Different farms can therefore produce fundamentally different bottlenecks even under the same crop and field conditions.

---

## 6. Dynamic System State

The current system state can be represented as:

\[
s_t=
\{
C_i,
G_j,
S_k,
D,
F
\}
\]

### Combine states

\[
C_i=
\{
position,
tank\ level,
harvest\ rate,
predicted\ fill\ time
\}
\]

### Grain cart states

\[
G_j=
\{
position,
load,
current\ task,
availability
\}
\]

### Semi states

\[
S_k=
\{
position,
load,
status,
return\ ETA
\}
\]

### Downstream states

\[
D=
\{
queue,
storage\ level,
dryer\ capacity,
service\ rate
\}
\]

### Field states

\[
F=
\{
harvested\ area,
remaining\ yield,
combine\ locations,
field\ accessibility
\}
\]

---

## 7. Predictive Model / World-Model Connection

A farm-level predictive model can be expressed as:

\[
p(
s_{t+1:t+H}
\mid
s_t,
a_{t:t+H},
c_F
)
\]

This is conceptually similar to a world model, but the state is the **whole farm logistics system**, rather than an individual tractor.

The model predicts what may happen under candidate coordination actions.

For example:

\[
A:
Cart_1\rightarrow Combine_1
\]

\[
B:
Cart_1\rightarrow Combine_2
\]

\[
C:
Cart_1\rightarrow Semi
\]

The system can simulate:

\[
(s_t,A)
\rightarrow
\hat{s}_{t+5}
\rightarrow
\hat{s}_{t+10}
\]

and compare this with the future resulting from \(B\) and \(C\).

A locally attractive action may produce poor system behavior later.

Prediction therefore allows the coordinator to consider:

\[
\boxed{
Current\ action
\rightarrow
Future\ system\ consequences
}
\]

rather than only immediate cost.

---

## 8. Structured / Hybrid World Model

The first paper does not need a large neural world model.

Many system dynamics are already known and can be modeled explicitly.

### Combine buffer

\[
B^C_{t+\Delta t}
=
B^C_t+
q_{harvest}\Delta t-
q_{unload}\Delta t
\]

### Grain cart buffer

\[
B^G_{t+\Delta t}
=
B^G_t+
q_{in}\Delta t-
q_{out}\Delta t
\]

### Downstream queue

\[
Q_{t+\Delta t}
=
Q_t+
Arrival-
Service
\]

### Semi travel

\[
T_{travel}
=
\frac{Distance}{Speed}
\]

Unknown or uncertain components can then be modeled probabilistically or learned from data:

- yield variation;
- semi travel time;
- road delay;
- dryer processing rate;
- machine delay;
- equipment failure.

Thus:

\[
\boxed{
Predictive\ Model
=
Known\ Dynamics
+
Stochastic/Learned\ Uncertainty
}
\]

For the first study, the term **predictive model-based coordination** may be more appropriate than forcing the work to use a neural world-model formulation.

---

## 9. Uncertainty

A predictive system should not assume perfect future information.

Examples:

\[
T_{\text{semi}}
\sim P(T)
\]

\[
Yield(x,y)
\sim P(Y)
\]

\[
\mu_{\text{dryer}}
\sim P(\mu)
\]

Instead of predicting one deterministic future, the coordinator can evaluate multiple possible futures:

\[
\hat{s}^{(1)},
\hat{s}^{(2)},
...,
\hat{s}^{(N)}
\]

This supports:

- Monte Carlo prediction;
- stochastic optimization;
- robust optimization;
- scenario-based planning;
- MPC / rolling-horizon coordination.

The goal is not necessarily to predict the future perfectly, but to make decisions that remain effective under uncertainty.

---

## 10. Dynamic Operating Strategy

Different bottlenecks can make different fleet coordination strategies preferable.

### Harvest-limited regime

If downstream capacity is abundant:

\[
\min T_{\text{combine idle}}
\]

The system may accept more cart travel, more fuel use, or semi waiting in exchange for keeping combines operating.

### Cart-limited regime

If grain carts are scarce, the system focuses on:

- cart-to-combine assignment;
- rendezvous timing;
- partial unloading;
- cart buffer utilization.

### Transport-limited regime

If semis are the limiting resource:

\[
T_{\text{semi cycle}}\uparrow
\]

the system may avoid unnecessary harvesting acceleration and preserve cart buffer capacity.

### Downstream-limited regime

If:

\[
HarvestRate
>
DownstreamCapacity
\]

maximizing harvest throughput may only create queues.

The preferable objective may become:

\[
\min
Fuel
+
Waiting
+
MachineHours
\]

subject to:

\[
HarvestRate
\approx
DownstreamCapacity
\]

### Weather-urgency regime

If rainfall or another deadline is approaching:

\[
P(rain)\uparrow
\]

the priority can shift toward:

\[
\min T_{\text{finish}}
\]

even if:

\[
Fuel\uparrow
\]

or:

\[
MachineHours\uparrow
\]

The important research question is therefore not simply:

> Which operating mode is best?

but:

> **Under what system conditions does each coordination policy become preferable?**

---

## 11. Current Research Questions

### Main Question

> **How can an autonomous harvest system predict emerging bottlenecks and dynamically adapt upstream fleet coordination according to field conditions, fleet states, and downstream logistics constraints?**

### RQ1

> **How do farm configuration and evolving downstream conditions change system bottlenecks and the relative effectiveness of alternative fleet coordination policies?**

This question tests whether:

\[
\boxed{
No\ single\ policy\ dominates\ all\ operating\ conditions
}
\]

### RQ2

> **Does predictive context-adaptive coordination outperform fixed and reactive coordination strategies in system-level harvest performance?**

This separates:

\[
Value\ of\ Adaptation
\]

from:

\[
Value\ of\ Prediction
\]

### RQ3

> **Under what combinations of system configuration, bottleneck severity, uncertainty, and prediction quality does predictive coordination provide meaningful advantages over fixed and reactive strategies?**

Predictive coordination is expected to be most valuable when:

\[
\boxed{
Near\ bottleneck
+
Uncertainty
+
Actionable\ flexibility
}
\]

are all present.

---

## 12. Experimental Comparison

### Policy A — Fixed Reactive

Uses a fixed dispatching rule.

Examples:

- nearest cart;
- highest-fill combine;
- cart full → unload.

### Policy B — Adaptive Reactive

Uses current system state to identify the existing bottleneck and change policy.

\[
s_t
\rightarrow
b_t
\rightarrow
a_t
\]

### Policy C — Adaptive Predictive

Uses predicted future states:

\[
s_t
\rightarrow
\hat{s}_{t:t+H}
\rightarrow
\hat{b}_{t+H}
\rightarrow
a_t
\]

This is the proposed method.

### Policy D — Oracle

Receives perfect future information:

\[
s_{t+1:t+H}^{true}
\]

This provides an upper bound on the value of prediction.

---

## 13. Key Evaluation Logic

The goal is not merely to show:

> Predictive coordination reduces harvest time by X%.

Instead, the study should establish:

### 1. Value of adaptation

\[
Fixed
\quad vs\quad
Reactive
\]

### 2. Value of prediction

\[
Reactive
\quad vs\quad
Predictive
\]

### 3. Value of better prediction

\[
Predictive
\quad vs\quad
Oracle
\]

### 4. Conditions under which prediction matters

Map performance across:

- fleet balance;
- downstream capacity;
- haul distance;
- yield variability;
- prediction error;
- disturbance severity.

The most useful result may therefore be a **regime map**, rather than a single average improvement.

---

## 14. Distinction from Previous Downstream Harvest Studies

Previous harvest logistics studies have already considered:

- transportation capacity;
- on-farm storage;
- commercial elevators;
- dryer capacity;
- downstream bottlenecks;
- farm-specific machinery configurations;
- capacity planning;
- what-if simulation.

Therefore, the novelty should **not** be stated as:

> We are the first to consider downstream logistics.

or:

> We are the first to show that dryer capacity affects harvesting.

The proposed distinction is:

### Previous system-level work

\[
Farm\ configuration
+
Downstream\ capacity
\rightarrow
System\ performance
\]

Typical questions:

- How many trucks are required?
- Is the dryer a bottleneck?
- How long will harvest take?
- Should storage capacity be increased?

### Proposed predictive autonomous system

\[
Observed\ state
+
Predicted\ future
\rightarrow
Real\text{-}time\ fleet\ coordination
\]

Typical question:

> Given that a transport or downstream bottleneck is likely to emerge in 20 minutes, what should each autonomous machine do now?

Thus the key transition is:

\[
\boxed{
Capacity\ analysis
\rightarrow
Operational\ prediction
\rightarrow
Autonomous\ coordination
}
\]

---

## 15. Why Harvest Is a Good Use Case

Harvesting is not the only agricultural operation involving fleets.

Other examples include:

- spraying fleets;
- UAV/UGV teams;
- seeding fleets;
- scouting robots;
- manure transport/application.

However, harvesting has a particularly strong system coupling because material continuously flows through several finite-capacity resources:

\[
Field
\rightarrow
Combine
\rightarrow
Cart
\rightarrow
Semi
\rightarrow
Processing
\]

This differs from many parallel field operations such as scouting or seeding, where machines primarily divide coverage tasks.

Harvest logistics is therefore better characterized as:

\[
\boxed{
Flow\text{-}coupled\ heterogeneous\ fleet
}
\]

rather than merely:

\[
Parallel\ machine\ fleet
\]

The combination of heterogeneous vehicles, finite mobile buffers, transportation, downstream processing, uncertain production rate, and moving bottlenecks makes harvesting a useful testbed for predictive farm-level coordination.

---

## 16. Current Conceptual Contribution

The project is moving from:

\[
\boxed{
Make\ individual\ machines\ autonomous
}
\]

toward:

\[
\boxed{
Make\ the\ entire\ farm\ operation\ intelligent
}
\]

The system-level intelligence layer answers:

> **What should each machine do next?**

while vehicle-level autonomy answers:

> **How should the machine execute that task?**

The proposed architecture is:

```text
                FARM-LEVEL INTELLIGENCE
        ┌───────────────────────────────┐
        │ Farm / fleet context          │
        │ Predictive system model       │
        │ Bottleneck prediction         │
        │ Adaptive fleet coordination   │
        └──────────────┬────────────────┘
                       │
                 task decisions
                       │
          ┌────────────┼────────────┐
          ↓            ↓            ↓
       Combine       Grain Cart    Semi
        Agent          Agent       Agent
          ↓             ↓           ↓
     local planner  local planner local planner
          ↓             ↓           ↓
       controller    controller   controller
```

The long-term concept can therefore be summarized as:

\[
\boxed{
Observe
\rightarrow
Predict
\rightarrow
Anticipate\ Bottleneck
\rightarrow
Coordinate
\rightarrow
Execute
\rightarrow
Observe
}
\]

This is the central predictive coordination loop for the proposed autonomous harvest system.
