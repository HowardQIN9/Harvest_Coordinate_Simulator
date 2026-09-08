from __future__ import annotations

from dataclasses import dataclass, field
from math import inf
from typing import Any

from .config import SimulationConfig

AT_FIELD = "AT_FIELD"
TO_GC = "TO_GC"
WAITING_UNLOAD = "WAITING_UNLOAD"
UNLOADING = "UNLOADING"
RETURNING = "RETURNING"


@dataclass
class GrainLot:
    amount_bu: float
    moisture_pct: float
    field_id: str


@dataclass
class Semi:
    semi_id: str
    lots: list[GrainLot] = field(default_factory=list)
    state: str = AT_FIELD
    gc_arrival_h: float = inf
    unload_complete_h: float = inf
    field_return_h: float = inf
    route_travel_h: float = 0.0
    route_field_id: str = ""
    trips: int = 0
    unload_initial_bu: float = 0.0
    unload_manifest: list[GrainLot] = field(default_factory=list)

    @property
    def load_bu(self) -> float:
        return sum(lot.amount_bu for lot in self.lots)


def _append_lot(queue: list[GrainLot], amount_bu: float,
                moisture_pct: float, field_id: str) -> None:
    if amount_bu <= 1e-12:
        return
    if (queue and queue[-1].moisture_pct == moisture_pct
            and queue[-1].field_id == field_id):
        queue[-1].amount_bu += amount_bu
    else:
        queue.append(GrainLot(amount_bu, moisture_pct, field_id))


def _move_lots(source: list[GrainLot], destination: list[GrainLot], amount_bu: float) -> list[GrainLot]:
    moved: list[GrainLot] = []
    remaining = amount_bu
    while remaining > 1e-10 and source:
        take = min(remaining, source[0].amount_bu)
        lot = source[0]
        _append_lot(destination, take, lot.moisture_pct, lot.field_id)
        _append_lot(moved, take, lot.moisture_pct, lot.field_id)
        lot.amount_bu -= take
        remaining -= take
        if lot.amount_bu <= 1e-9:
            source.pop(0)
    return moved


class Downstream:
    """Semi transport, receiving pit, grain leg, wet bin, and FIFO dryer."""

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self.time_h = 0.0
        self.combine_lots: list[GrainLot] = []
        self.semis = [Semi(f"Semi{i + 1}") for i in range(config.semi_count)]
        self.receiving_pit_lots: list[GrainLot] = []
        self.wet_bin_lots: list[GrainLot] = []
        self.current_field_id = config.field_specs[0].field_id if config.field_specs else ""
        self.current_travel_h = ((config.field_specs[0].one_way_travel_min / 60.0)
                                 if config.field_specs else 0.0)
        self.total_unloaded_bu = 0.0
        self.total_leg_transferred_bu = 0.0
        self.total_dried_bu = 0.0
        self.total_dryer_operating_h = 0.0
        self.total_dried_moisture_bushel_pct = 0.0
        self.dried_by_field_bu: dict[str, float] = {}
        self.events: list[dict[str, Any]] = []

    @property
    def combine_tank_bu(self) -> float:
        return sum(lot.amount_bu for lot in self.combine_lots)

    @property
    def receiving_pit_inventory_bu(self) -> float:
        return sum(lot.amount_bu for lot in self.receiving_pit_lots)

    @property
    def wet_bin_inventory_bu(self) -> float:
        return sum(lot.amount_bu for lot in self.wet_bin_lots)

    @property
    def semi_at_field_grain_bu(self) -> float:
        return sum(semi.load_bu for semi in self.semis if semi.state == AT_FIELD)

    @property
    def in_transit_grain_bu(self) -> float:
        return sum(semi.load_bu for semi in self.semis if semi.state == TO_GC)

    @property
    def semi_at_gc_grain_bu(self) -> float:
        return sum(semi.load_bu for semi in self.semis
                   if semi.state in (WAITING_UNLOAD, UNLOADING))

    @property
    def unprocessed_grain_bu(self) -> float:
        return (self.combine_tank_bu + sum(semi.load_bu for semi in self.semis)
                + self.receiving_pit_inventory_bu + self.wet_bin_inventory_bu)

    @property
    def receiving_pressure(self) -> float:
        c = self.config
        free = c.combine_tank_capacity_bu - self.combine_tank_bu
        free += sum(c.semi_capacity_bu - semi.load_bu
                    for semi in self.semis if semi.state == AT_FIELD)
        return 1.0 - free / (c.combine_tank_capacity_bu + c.semi_count * c.semi_capacity_bu)

    @property
    def current_dryer_rate_buph(self) -> float:
        unloading = next((semi for semi in self.semis
                          if semi.state == UNLOADING and semi.lots), None)
        queue = (self.wet_bin_lots or self.receiving_pit_lots
                 or (unloading.lots if unloading else []))
        if not queue:
            return 0.0
        return self.config.dryer_rate_for_moisture(queue[0].moisture_pct)

    @property
    def next_event_h(self) -> float:
        times = [semi.gc_arrival_h for semi in self.semis if semi.state == TO_GC]
        times += [semi.unload_complete_h for semi in self.semis if semi.state == UNLOADING]
        times += [semi.field_return_h for semi in self.semis if semi.state == RETURNING]
        return min(times, default=inf)

    def set_field_context(self, field_id: str, one_way_travel_min: float) -> None:
        self.current_field_id = field_id
        self.current_travel_h = one_way_travel_min / 60.0

    def add_harvested_grain(self, grain_bu: float,
                            moisture_pct: float, field_id: str) -> None:
        _append_lot(self.combine_lots, grain_bu, moisture_pct, field_id)

    def _dryer_rate(self, moisture_pct: float) -> float:
        return self.config.dryer_rate_for_moisture(moisture_pct)

    def dryer_inventory_target_bu(
            self, horizon_h: float,
            fallback_moisture_pct: float | None = None) -> float:
        """FIFO-aware grain quantity the dryer can process over ``horizon_h``.

        Lots already moving through the downstream system are followed in their
        expected order. If they do not cover the horizon, newly harvested grain
        is represented by the active field's fixed moisture.
        """
        remaining_h = max(0.0, horizon_h)
        required_bu = 0.0
        semis_in_expected_order = sorted(
            self.semis,
            key=lambda semi: min(semi.gc_arrival_h, semi.unload_complete_h,
                                 semi.field_return_h),
        )
        queues = [self.wet_bin_lots, self.receiving_pit_lots]
        queues.extend(semi.lots for semi in semis_in_expected_order)
        queues.append(self.combine_lots)
        for queue in queues:
            for lot in queue:
                if remaining_h <= self.config.numerical_tolerance:
                    break
                rate = self._dryer_rate(lot.moisture_pct)
                amount = min(lot.amount_bu, rate * remaining_h)
                required_bu += amount
                remaining_h -= amount / rate
            if remaining_h <= self.config.numerical_tolerance:
                break
        if (remaining_h > self.config.numerical_tolerance
                and fallback_moisture_pct is not None):
            required_bu += (
                self._dryer_rate(fallback_moisture_pct) * remaining_h)
        return min(self.config.wet_bin_capacity_bu, required_bu)

    def _dry(self, amount_bu: float) -> float:
        if amount_bu <= self.config.numerical_tolerance:
            return 0.0
        dried_lots: list[GrainLot] = []
        _move_lots(self.wet_bin_lots, dried_lots, amount_bu)
        dried_bu = sum(lot.amount_bu for lot in dried_lots)
        for lot in dried_lots:
            self.total_dried_moisture_bushel_pct += lot.amount_bu * lot.moisture_pct
            self.dried_by_field_bu[lot.field_id] = (
                self.dried_by_field_bu.get(lot.field_id, 0.0) + lot.amount_bu)
        return dried_bu

    def _flow_rates(self, unloading: Semi | None) -> tuple[float, float, float]:
        """Return current semi-unload, grain-leg, and dryer flow rates."""
        c = self.config
        pit = self.receiving_pit_inventory_bu
        wet = self.wet_bin_inventory_bu
        head_queue = (self.wet_bin_lots or self.receiving_pit_lots
                      or (unloading.lots if unloading else []))
        dryer_rate = self._dryer_rate(head_queue[0].moisture_pct) if head_queue else 0.0
        nominal_unload_rate = c.semi_capacity_bu / c.semi_unload_time_h
        source_unload_rate = (
            nominal_unload_rate
            if unloading is not None and unloading.load_bu > c.numerical_tolerance
            else 0.0
        )

        if wet < c.wet_bin_capacity_bu - c.numerical_tolerance:
            leg_capacity = c.grain_leg_rate_buph
        elif dryer_rate > 0:
            # At a full wet bin the leg can replace only what the dryer removes.
            leg_capacity = min(c.grain_leg_rate_buph, dryer_rate)
        else:
            leg_capacity = 0.0

        if pit > c.numerical_tolerance:
            leg_rate = leg_capacity
        else:
            # Grain may pass straight through an empty pit while the semi
            # unloads; the leg cannot move faster than that incoming flow.
            leg_rate = min(leg_capacity, source_unload_rate)

        if pit < c.receiving_pit_capacity_bu - c.numerical_tolerance:
            unload_rate = source_unload_rate
        else:
            # A full pit throttles the semi to exactly the rate at which the
            # grain leg creates room. If that rate is zero, unloading pauses.
            unload_rate = min(source_unload_rate, leg_rate)

        active_dryer_rate = (
            dryer_rate if wet > c.numerical_tolerance
            else min(dryer_rate, leg_rate)
        )
        return unload_rate, leg_rate, active_dryer_rate

    def advance_time(self, duration_h: float) -> None:
        c = self.config
        if duration_h < 0 or self.time_h + duration_h > self.next_event_h + c.numerical_tolerance:
            raise ValueError("Split the interval at the next downstream event")
        remaining_h = duration_h
        unloading = next((semi for semi in self.semis if semi.state == UNLOADING), None)
        iterations = 0
        while remaining_h > c.numerical_tolerance:
            iterations += 1
            if iterations > 10000:
                raise RuntimeError("Continuous downstream flow did not converge")
            pit = self.receiving_pit_inventory_bu
            wet = self.wet_bin_inventory_bu
            unload_rate, leg_rate, active_dryer_rate = self._flow_rates(unloading)
            if (unload_rate <= c.numerical_tolerance
                    and leg_rate <= c.numerical_tolerance
                    and active_dryer_rate <= c.numerical_tolerance):
                break

            step = remaining_h
            if (unload_rate > c.numerical_tolerance and unloading is not None
                    and unloading.lots):
                step = min(step, unloading.lots[0].amount_bu / unload_rate)
            if leg_rate > c.numerical_tolerance and self.receiving_pit_lots:
                step = min(step, self.receiving_pit_lots[0].amount_bu / leg_rate)
            if active_dryer_rate > c.numerical_tolerance and self.wet_bin_lots:
                step = min(step, self.wet_bin_lots[0].amount_bu / active_dryer_rate)
            pit_net_rate = unload_rate - leg_rate
            if (pit_net_rate > c.numerical_tolerance
                    and pit < c.receiving_pit_capacity_bu):
                step = min(step, (c.receiving_pit_capacity_bu - pit) / pit_net_rate)
            elif pit_net_rate < -c.numerical_tolerance and pit > 0:
                step = min(step, pit / -pit_net_rate)
            wet_net_rate = leg_rate - active_dryer_rate
            if (wet_net_rate > c.numerical_tolerance
                    and wet < c.wet_bin_capacity_bu):
                step = min(step, (c.wet_bin_capacity_bu - wet) / wet_net_rate)
            elif wet_net_rate < -c.numerical_tolerance and wet > 0:
                step = min(step, wet / -wet_net_rate)
            if step <= c.numerical_tolerance:
                step = min(remaining_h, c.numerical_tolerance)

            unload_amount = unload_rate * step
            leg_amount = leg_rate * step
            dry_amount = active_dryer_rate * step
            # Upstream-to-downstream ordering permits continuous pass-through
            # when a buffer starts empty. Temporary within-step overfill is
            # removed by the simultaneous downstream flow before state is read.
            if unloading is not None:
                moved = _move_lots(
                    unloading.lots, self.receiving_pit_lots, unload_amount)
                self.total_unloaded_bu += sum(lot.amount_bu for lot in moved)
            moved = _move_lots(
                self.receiving_pit_lots, self.wet_bin_lots, leg_amount)
            self.total_leg_transferred_bu += sum(lot.amount_bu for lot in moved)
            dried_bu = self._dry(dry_amount)
            self.total_dried_bu += dried_bu
            if active_dryer_rate > c.numerical_tolerance:
                self.total_dryer_operating_h += step
            remaining_h -= step

        self.time_h += duration_h
        if unloading is not None:
            if unloading.load_bu <= c.numerical_tolerance:
                unloading.lots.clear()
                unloading.unload_complete_h = self.time_h
            else:
                current_unload_rate, _, _ = self._flow_rates(unloading)
                unloading.unload_complete_h = (
                    self.time_h + unloading.load_bu / current_unload_rate
                    if current_unload_rate > c.numerical_tolerance else inf
                )

    def _moisture_label(self, lots: list[GrainLot]) -> str:
        values = sorted({lot.moisture_pct for lot in lots})
        return "+".join(f"{value:.4f}" for value in values)

    def _field_label(self, lots: list[GrainLot]) -> str:
        values = []
        for lot in lots:
            if lot.field_id not in values:
                values.append(lot.field_id)
        return "+".join(values)

    def _event(self, kind: str, semi: Semi, grain_bu: float,
               lots: list[GrainLot] | None = None) -> None:
        event_lots = semi.lots if lots is None else lots
        self.events.append({
            "time_h": self.time_h, "event": kind, "semi_id": semi.semi_id,
            "grain_bu": grain_bu, "trip": semi.trips,
            "moisture_pct": self._moisture_label(event_lots),
            "grain_field_ids": self._field_label(event_lots),
            "route_field_id": semi.route_field_id,
            "one_way_travel_min": semi.route_travel_h * 60.0,
        })

    def _depart(self, semi: Semi) -> None:
        semi.state = TO_GC
        semi.route_travel_h = self.current_travel_h
        semi.route_field_id = self.current_field_id
        semi.gc_arrival_h = self.time_h + semi.route_travel_h
        semi.trips += 1
        self._event("DEPART_FIELD", semi, semi.load_bu)

    def _try_start_unloading(self) -> None:
        c = self.config
        if any(semi.state == UNLOADING for semi in self.semis):
            return
        waiting = sorted((semi for semi in self.semis if semi.state == WAITING_UNLOAD),
                         key=lambda semi: (semi.gc_arrival_h, semi.semi_id))
        if waiting:
            semi = waiting[0]
            semi.state = UNLOADING
            semi.unload_initial_bu = semi.load_bu
            semi.unload_manifest = [
                GrainLot(lot.amount_bu, lot.moisture_pct, lot.field_id)
                for lot in semi.lots
            ]
            current_rate, _, _ = self._flow_rates(semi)
            semi.unload_complete_h = (
                self.time_h + semi.load_bu / current_rate
                if current_rate > c.numerical_tolerance else inf
            )
            self._event("START_UNLOAD", semi, semi.load_bu)

    def handle_events(self) -> None:
        c = self.config
        tolerance = c.numerical_tolerance
        for semi in self.semis:
            if semi.state == TO_GC and semi.gc_arrival_h <= self.time_h + tolerance:
                semi.state = WAITING_UNLOAD
                semi.gc_arrival_h = inf
                self._event("ARRIVE_GC", semi, semi.load_bu)
        for semi in self.semis:
            if (semi.state == UNLOADING
                    and semi.load_bu <= tolerance
                    and semi.unload_complete_h <= self.time_h + tolerance):
                self._event("FINISH_UNLOAD", semi, semi.unload_initial_bu,
                            semi.unload_manifest)
                semi.state = RETURNING
                semi.unload_complete_h = inf
                semi.field_return_h = self.time_h + semi.route_travel_h
                semi.unload_initial_bu = 0.0
                semi.unload_manifest = []
        for semi in self.semis:
            if semi.state == RETURNING and semi.field_return_h <= self.time_h + tolerance:
                semi.state = AT_FIELD
                semi.field_return_h = inf
                self._event("RETURN_FIELD", semi, 0.0, [])
        self._try_start_unloading()

    def unload_combine(self, force: bool = False) -> None:
        c = self.config
        if not force and self.combine_tank_bu < c.combine_tank_capacity_bu - c.numerical_tolerance:
            return
        available = sorted((semi for semi in self.semis if semi.state == AT_FIELD),
                           key=lambda semi: (-semi.load_bu, semi.semi_id))
        for semi in available:
            transferred = min(self.combine_tank_bu, c.semi_capacity_bu - semi.load_bu)
            if transferred > c.numerical_tolerance:
                moved = _move_lots(self.combine_lots, semi.lots, transferred)
                self._event("LOAD_SEMI", semi, transferred, moved)
            if semi.load_bu >= c.semi_capacity_bu - c.numerical_tolerance:
                self._depart(semi)

    def dispatch_final_loads(self) -> None:
        self.unload_combine(force=True)
        for semi in self.semis:
            if semi.state == AT_FIELD and semi.load_bu > self.config.numerical_tolerance:
                self._depart(semi)

    def _weighted_moisture_pct(self, lots: list[GrainLot]) -> float:
        total_bu = sum(lot.amount_bu for lot in lots)
        if total_bu <= self.config.numerical_tolerance:
            return 0.0
        return sum(lot.amount_bu * lot.moisture_pct for lot in lots) / total_bu

    def snapshot(self) -> dict[str, Any]:
        dryer_queue = self.wet_bin_lots or self.receiving_pit_lots
        row: dict[str, Any] = {
            "combine_tank_bu": self.combine_tank_bu,
            "semi_at_field_grain_bu": self.semi_at_field_grain_bu,
            "field_grain_bu": self.combine_tank_bu + self.semi_at_field_grain_bu,
            "in_transit_grain_bu": self.in_transit_grain_bu,
            "semi_at_gc_grain_bu": self.semi_at_gc_grain_bu,
            "receiving_pit_inventory_bu": self.receiving_pit_inventory_bu,
            "wet_bin_inventory_bu": self.wet_bin_inventory_bu,
            "wet_bin_head_moisture_pct": (self.wet_bin_lots[0].moisture_pct
                                           if self.wet_bin_lots else 0.0),
            "wet_bin_average_moisture_pct": self._weighted_moisture_pct(
                self.wet_bin_lots),
            "dryer_head_moisture_pct": (dryer_queue[0].moisture_pct
                                          if dryer_queue else 0.0),
            "dryer_rate_buph": self.current_dryer_rate_buph,
            "semis_at_field": sum(semi.state == AT_FIELD for semi in self.semis),
            "semis_waiting_at_gc": sum(semi.state == WAITING_UNLOAD for semi in self.semis),
            "receiving_pressure": self.receiving_pressure,
            "cumulative_grain_unloaded_bu": self.total_unloaded_bu,
            "cumulative_grain_to_wet_bin_bu": self.total_leg_transferred_bu,
            "cumulative_grain_dried_bu": self.total_dried_bu,
            "cumulative_dryer_operating_h": self.total_dryer_operating_h,
            "cumulative_dried_average_moisture_pct": (
                self.total_dried_moisture_bushel_pct / self.total_dried_bu
                if self.total_dried_bu > self.config.numerical_tolerance else 0.0),
            # Compatibility aliases for older output consumers.
            "grain_center_inventory_bu": self.wet_bin_inventory_bu,
            "cumulative_grain_received_bu": self.total_unloaded_bu,
            "cumulative_grain_processed_bu": self.total_dried_bu,
        }
        for index, semi in enumerate(self.semis, start=1):
            row[f"semi_{index}_state"] = semi.state
            row[f"semi_{index}_load_bu"] = semi.load_bu
        return row
