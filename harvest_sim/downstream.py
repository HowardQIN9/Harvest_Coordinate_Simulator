from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Any

from .config import SimulationConfig

AT_FIELD = "AT_FIELD"
TO_GC = "TO_GC"
RETURNING = "RETURNING"


@dataclass
class Semi:
    semi_id: str
    load_bu: float = 0.0
    state: str = AT_FIELD
    gc_arrival_h: float = inf
    field_return_h: float = inf
    trips: int = 0


class Downstream:
    """Mobile grain storage, discrete semi trips, and continuous GC processing.

    Split intervals at next_event_h, advance continuous time, then handle events.
    GC never processes a load before its arrival, regardless of the time step.
    """

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self.time_h = 0.0
        self.combine_tank_bu = 0.0
        self.semis = [Semi(f"Semi{i + 1}") for i in range(config.semi_count)]
        self.grain_center_inventory_bu = 0.0
        self.total_received_bu = 0.0
        self.total_processed_bu = 0.0
        self.events: list[dict[str, Any]] = []

    @property
    def semi_at_field_grain_bu(self) -> float:
        return sum(semi.load_bu for semi in self.semis if semi.state == AT_FIELD)

    @property
    def in_transit_grain_bu(self) -> float:
        return sum(semi.load_bu for semi in self.semis if semi.state == TO_GC)

    @property
    def unprocessed_grain_bu(self) -> float:
        return (self.combine_tank_bu + self.semi_at_field_grain_bu
                + self.in_transit_grain_bu + self.grain_center_inventory_bu)

    @property
    def receiving_pressure(self) -> float:
        c = self.config
        free = c.combine_tank_capacity_bu - self.combine_tank_bu
        free += sum(c.semi_capacity_bu - semi.load_bu for semi in self.semis if semi.state == AT_FIELD)
        return 1.0 - free / (c.combine_tank_capacity_bu + c.semi_count * c.semi_capacity_bu)

    @property
    def next_event_h(self) -> float:
        times = [semi.gc_arrival_h for semi in self.semis if semi.state == TO_GC]
        times += [semi.field_return_h for semi in self.semis if semi.state == RETURNING]
        if self.grain_center_inventory_bu > self.config.numerical_tolerance:
            times.append(self.time_h + self.grain_center_inventory_bu /
                         self.config.grain_center_processing_rate_buph)
        return min(times, default=inf)

    def advance_time(self, duration_h: float) -> None:
        if duration_h < 0 or self.time_h + duration_h > self.next_event_h + self.config.numerical_tolerance:
            raise ValueError("Split the interval at the next downstream event")
        processed = min(self.grain_center_inventory_bu,
                        self.config.grain_center_processing_rate_buph * duration_h)
        self.grain_center_inventory_bu -= processed
        self.total_processed_bu += processed
        self.time_h += duration_h

    def _event(self, kind: str, semi: Semi, grain_bu: float) -> None:
        self.events.append({"time_h": self.time_h, "event": kind,
                            "semi_id": semi.semi_id, "grain_bu": grain_bu,
                            "trip": semi.trips})

    def _depart(self, semi: Semi) -> None:
        semi.state = TO_GC
        semi.gc_arrival_h = self.time_h + self.config.semi_gc_arrival_h
        semi.field_return_h = self.time_h + self.config.semi_round_trip_h
        semi.trips += 1
        self._event("DEPART_FIELD", semi, semi.load_bu)

    def handle_events(self) -> None:
        tolerance = self.config.numerical_tolerance
        for semi in self.semis:
            if semi.state == TO_GC and semi.gc_arrival_h <= self.time_h + tolerance:
                self.grain_center_inventory_bu += semi.load_bu
                self.total_received_bu += semi.load_bu
                self._event("ARRIVE_GC", semi, semi.load_bu)
                semi.load_bu = 0.0
                semi.state = RETURNING
                semi.gc_arrival_h = inf
            if semi.state == RETURNING and semi.field_return_h <= self.time_h + tolerance:
                semi.state = AT_FIELD
                semi.field_return_h = inf
                self._event("RETURN_FIELD", semi, 0.0)

    def unload_combine(self, force: bool = False) -> None:
        """Unload a full tank instantly; flush partial tanks after harvest.

        Fill the most-loaded on-site semi first. A full semi departs immediately.
        Neither the combine nor trucks ever unload grain onto the ground.
        """
        c = self.config
        if not force and self.combine_tank_bu < c.combine_tank_capacity_bu - c.numerical_tolerance:
            return
        available = sorted((semi for semi in self.semis if semi.state == AT_FIELD),
                           key=lambda semi: (-semi.load_bu, semi.semi_id))
        for semi in available:
            transferred = min(self.combine_tank_bu, c.semi_capacity_bu - semi.load_bu)
            if transferred > c.numerical_tolerance:
                self.combine_tank_bu -= transferred
                semi.load_bu += transferred
                self._event("LOAD_SEMI", semi, transferred)
            if semi.load_bu >= c.semi_capacity_bu - c.numerical_tolerance:
                self._depart(semi)

    def dispatch_final_loads(self) -> None:
        """The last shipment may be partial so all harvested grain reaches GC."""
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
            "grain_center_inventory_bu": self.grain_center_inventory_bu,
            "semis_at_field": sum(semi.state == AT_FIELD for semi in self.semis),
            "receiving_pressure": self.receiving_pressure,
            "cumulative_grain_received_bu": self.total_received_bu,
            "cumulative_grain_processed_bu": self.total_processed_bu,
        }
        for index, semi in enumerate(self.semis, start=1):
            row[f"semi_{index}_state"] = semi.state
            row[f"semi_{index}_load_bu"] = semi.load_bu
        return row
