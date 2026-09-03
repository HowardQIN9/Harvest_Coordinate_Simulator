from __future__ import annotations

from dataclasses import dataclass, field
from math import inf
from typing import Any

from .config import HIGH, LOW, SimulationConfig

AT_FIELD = "AT_FIELD"
TO_GC = "TO_GC"
WAITING_UNLOAD = "WAITING_UNLOAD"
UNLOADING = "UNLOADING"
RETURNING = "RETURNING"


@dataclass
class GrainLot:
    amount_bu: float
    moisture: str
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

    @property
    def load_bu(self) -> float:
        return sum(lot.amount_bu for lot in self.lots)


def _append_lot(queue: list[GrainLot], amount_bu: float, moisture: str, field_id: str) -> None:
    if amount_bu <= 1e-12:
        return
    if queue and queue[-1].moisture == moisture and queue[-1].field_id == field_id:
        queue[-1].amount_bu += amount_bu
    else:
        queue.append(GrainLot(amount_bu, moisture, field_id))


def _move_lots(source: list[GrainLot], destination: list[GrainLot], amount_bu: float) -> list[GrainLot]:
    moved: list[GrainLot] = []
    remaining = amount_bu
    while remaining > 1e-10 and source:
        take = min(remaining, source[0].amount_bu)
        lot = source[0]
        _append_lot(destination, take, lot.moisture, lot.field_id)
        _append_lot(moved, take, lot.moisture, lot.field_id)
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
        self.dried_by_moisture_bu = {HIGH: 0.0, LOW: 0.0}
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
        queue = self.wet_bin_lots or self.receiving_pit_lots
        if not queue:
            return 0.0
        return (self.config.dryer_high_moisture_rate_buph
                if queue[0].moisture == HIGH
                else self.config.dryer_low_moisture_rate_buph)

    @property
    def next_event_h(self) -> float:
        times = [semi.gc_arrival_h for semi in self.semis if semi.state == TO_GC]
        times += [semi.unload_complete_h for semi in self.semis if semi.state == UNLOADING]
        times += [semi.field_return_h for semi in self.semis if semi.state == RETURNING]
        return min(times, default=inf)

    def set_field_context(self, field_id: str, one_way_travel_min: float) -> None:
        self.current_field_id = field_id
        self.current_travel_h = one_way_travel_min / 60.0

    def add_harvested_grain(self, grain_bu: float, moisture: str, field_id: str) -> None:
        _append_lot(self.combine_lots, grain_bu, moisture, field_id)

    def _dryer_rate(self, moisture: str) -> float:
        return (self.config.dryer_high_moisture_rate_buph
                if moisture == HIGH else self.config.dryer_low_moisture_rate_buph)

    def _dry(self, amount_bu: float) -> None:
        if amount_bu <= self.config.numerical_tolerance:
            return
        dried_lots: list[GrainLot] = []
        _move_lots(self.wet_bin_lots, dried_lots, amount_bu)
        for lot in dried_lots:
            self.dried_by_moisture_bu[lot.moisture] += lot.amount_bu

    def advance_time(self, duration_h: float) -> None:
        c = self.config
        if duration_h < 0 or self.time_h + duration_h > self.next_event_h + c.numerical_tolerance:
            raise ValueError("Split the interval at the next downstream event")
        remaining_h = duration_h
        iterations = 0
        while remaining_h > c.numerical_tolerance:
            iterations += 1
            if iterations > 10000:
                raise RuntimeError("Continuous downstream flow did not converge")
            pit = self.receiving_pit_inventory_bu
            wet = self.wet_bin_inventory_bu
            head_queue = self.wet_bin_lots or self.receiving_pit_lots
            dryer_rate = self._dryer_rate(head_queue[0].moisture) if head_queue else 0.0

            if pit <= c.numerical_tolerance:
                leg_rate = 0.0
            elif wet < c.wet_bin_capacity_bu - c.numerical_tolerance:
                leg_rate = c.grain_leg_rate_buph
            elif dryer_rate > 0:
                # At a full wet bin the leg can replace only what the dryer removes.
                leg_rate = min(c.grain_leg_rate_buph, dryer_rate)
            else:
                leg_rate = 0.0

            active_dryer_rate = dryer_rate if wet > c.numerical_tolerance else min(dryer_rate, leg_rate)
            if leg_rate <= c.numerical_tolerance and active_dryer_rate <= c.numerical_tolerance:
                break

            step = remaining_h
            if leg_rate > c.numerical_tolerance and self.receiving_pit_lots:
                step = min(step, self.receiving_pit_lots[0].amount_bu / leg_rate)
            if active_dryer_rate > c.numerical_tolerance and self.wet_bin_lots:
                step = min(step, self.wet_bin_lots[0].amount_bu / active_dryer_rate)
            net_rate = leg_rate - active_dryer_rate
            if net_rate > c.numerical_tolerance and wet < c.wet_bin_capacity_bu:
                step = min(step, (c.wet_bin_capacity_bu - wet) / net_rate)
            elif net_rate < -c.numerical_tolerance and wet > 0:
                step = min(step, wet / -net_rate)
            if step <= c.numerical_tolerance:
                step = min(remaining_h, c.numerical_tolerance)

            leg_amount = leg_rate * step
            dry_amount = active_dryer_rate * step
            if wet >= c.wet_bin_capacity_bu - c.numerical_tolerance:
                self._dry(dry_amount)
                _move_lots(self.receiving_pit_lots, self.wet_bin_lots, leg_amount)
            else:
                _move_lots(self.receiving_pit_lots, self.wet_bin_lots, leg_amount)
                self._dry(dry_amount)
            self.total_leg_transferred_bu += leg_amount
            self.total_dried_bu += dry_amount
            remaining_h -= step

        self.time_h += duration_h

    def _moisture_label(self, lots: list[GrainLot]) -> str:
        values = sorted({lot.moisture for lot in lots})
        return "+".join(values)

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
            "moisture": self._moisture_label(event_lots),
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
        free_pit = c.receiving_pit_capacity_bu - self.receiving_pit_inventory_bu
        waiting = sorted((semi for semi in self.semis if semi.state == WAITING_UNLOAD),
                         key=lambda semi: (semi.gc_arrival_h, semi.semi_id))
        for semi in waiting:
            if semi.load_bu <= free_pit + c.numerical_tolerance:
                semi.state = UNLOADING
                semi.unload_complete_h = self.time_h + c.semi_unload_time_h
                self._event("START_UNLOAD", semi, semi.load_bu)
                break

    def handle_events(self) -> None:
        c = self.config
        tolerance = c.numerical_tolerance
        for semi in self.semis:
            if semi.state == TO_GC and semi.gc_arrival_h <= self.time_h + tolerance:
                semi.state = WAITING_UNLOAD
                semi.gc_arrival_h = inf
                self._event("ARRIVE_GC", semi, semi.load_bu)
        for semi in self.semis:
            if semi.state == UNLOADING and semi.unload_complete_h <= self.time_h + tolerance:
                grain = semi.load_bu
                lots = [GrainLot(lot.amount_bu, lot.moisture, lot.field_id) for lot in semi.lots]
                _move_lots(semi.lots, self.receiving_pit_lots, grain)
                self.total_unloaded_bu += grain
                self._event("FINISH_UNLOAD", semi, grain, lots)
                semi.state = RETURNING
                semi.unload_complete_h = inf
                semi.field_return_h = self.time_h + semi.route_travel_h
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

    def snapshot(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "combine_tank_bu": self.combine_tank_bu,
            "semi_at_field_grain_bu": self.semi_at_field_grain_bu,
            "field_grain_bu": self.combine_tank_bu + self.semi_at_field_grain_bu,
            "in_transit_grain_bu": self.in_transit_grain_bu,
            "semi_at_gc_grain_bu": self.semi_at_gc_grain_bu,
            "receiving_pit_inventory_bu": self.receiving_pit_inventory_bu,
            "wet_bin_inventory_bu": self.wet_bin_inventory_bu,
            "wet_bin_high_moisture_bu": sum(lot.amount_bu for lot in self.wet_bin_lots if lot.moisture == HIGH),
            "wet_bin_low_moisture_bu": sum(lot.amount_bu for lot in self.wet_bin_lots if lot.moisture != HIGH),
            "wet_bin_first_two_fields_bu": sum(lot.amount_bu for lot in self.wet_bin_lots if lot.moisture == HIGH),
            "wet_bin_remaining_fields_bu": sum(lot.amount_bu for lot in self.wet_bin_lots if lot.moisture != HIGH),
            "dryer_rate_buph": self.current_dryer_rate_buph,
            "semis_at_field": sum(semi.state == AT_FIELD for semi in self.semis),
            "semis_waiting_at_gc": sum(semi.state == WAITING_UNLOAD for semi in self.semis),
            "receiving_pressure": self.receiving_pressure,
            "cumulative_grain_unloaded_bu": self.total_unloaded_bu,
            "cumulative_grain_to_wet_bin_bu": self.total_leg_transferred_bu,
            "cumulative_grain_dried_bu": self.total_dried_bu,
            "cumulative_dried_high_bu": self.dried_by_moisture_bu[HIGH],
            "cumulative_dried_low_bu": self.dried_by_moisture_bu[LOW],
            "cumulative_dried_first_two_fields_bu": self.dried_by_moisture_bu[HIGH],
            "cumulative_dried_remaining_fields_bu": self.dried_by_moisture_bu[LOW],
            # Compatibility aliases for older output consumers.
            "grain_center_inventory_bu": self.wet_bin_inventory_bu,
            "cumulative_grain_received_bu": self.total_unloaded_bu,
            "cumulative_grain_processed_bu": self.total_dried_bu,
        }
        for index, semi in enumerate(self.semis, start=1):
            row[f"semi_{index}_state"] = semi.state
            row[f"semi_{index}_load_bu"] = semi.load_bu
        return row
