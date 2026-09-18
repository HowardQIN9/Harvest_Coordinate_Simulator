from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import inf
from typing import Any, Iterable

from .config import SimulationConfig


AT_FIELD = "AT_FIELD"
TO_GC = "TO_GC"
WAITING_UNLOAD = "WAITING_UNLOAD"
UNLOADING = "UNLOADING"
RETURNING = "RETURNING"

COMBINE = "COMBINE"
GRAIN_CART = "GRAIN_CART"
SEMI = "SEMI"


@dataclass
class GrainLot:
    amount_bu: float
    moisture_pct: float
    field_id: str


@dataclass
class Vehicle:
    """One physical mobile unit with independent storage and state."""

    vehicle_id: str
    vehicle_type: str
    harvest_capacity_buph: float
    storage_capacity_bu: float
    lots: list[GrainLot] = field(default_factory=list)
    state: str = AT_FIELD
    location: str = "FIELD"
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

    @property
    def free_storage_bu(self) -> float:
        return max(0.0, self.storage_capacity_bu - self.load_bu)


# Compatibility type name for older imports.
Semi = Vehicle


def _append_lot(queue: list[GrainLot], amount_bu: float,
                moisture_pct: float, field_id: str) -> None:
    if amount_bu <= 1e-12:
        return
    if (queue and queue[-1].moisture_pct == moisture_pct
            and queue[-1].field_id == field_id):
        queue[-1].amount_bu += amount_bu
    else:
        queue.append(GrainLot(amount_bu, moisture_pct, field_id))


def _move_lots(
        source: list[GrainLot], destination: list[GrainLot],
        amount_bu: float) -> list[GrainLot]:
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
    """Independent vehicles, semi transport, wet bin, and FIFO dryer."""

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self.time_h = 0.0
        self.combines = [
            Vehicle(
                f"Combine{i + 1}", COMBINE,
                config.combine_harvest_capacity_buph,
                config.combine_storage_capacity_bu,
            )
            for i in range(config.n_combines)
        ]
        self.grain_carts = [
            Vehicle(
                f"GrainCart{i + 1}", GRAIN_CART,
                config.grain_cart_harvest_capacity_buph,
                config.grain_cart_storage_capacity_bu,
            )
            for i in range(config.n_grain_carts)
        ]
        self.semis = [
            Vehicle(
                f"Semi{i + 1}", SEMI, 0.0,
                config.semi_storage_capacity_bu,
            )
            for i in range(config.n_semis)
        ]
        self.active_combine_count = config.n_combines
        self.active_grain_cart_count = config.n_grain_carts
        self.wet_bin_lots: list[GrainLot] = []
        self.current_field_id = (
            config.field_specs[0].field_id if config.field_specs else "")
        self.current_travel_h = (
            config.field_specs[0].one_way_travel_min / 60.0
            if config.field_specs else 0.0)
        self.total_unloaded_bu = 0.0
        self.total_leg_transferred_bu = 0.0
        self.total_dried_bu = 0.0
        self.total_dryer_operating_h = 0.0
        self.dryer_has_started = False
        self.current_dryer_idle_episode_h = 0.0
        self.maximum_dryer_idle_episode_h = 0.0
        self.total_dryer_starvation_h = 0.0
        self.total_dried_moisture_bushel_pct = 0.0
        self.dried_by_field_bu: dict[str, float] = {}
        self.events: list[dict[str, Any]] = []
        self.record_events = True
        self._radio_eta_signature: tuple[Any, ...] | None = None
        self._radio_return_schedule: tuple[tuple[float, float], ...] = ()

    @property
    def vehicles(self) -> tuple[Vehicle, ...]:
        return tuple(self.combines + self.grain_carts + self.semis)

    @property
    def active_combines(self) -> list[Vehicle]:
        return self.combines[:self.active_combine_count]

    @property
    def active_grain_carts(self) -> list[Vehicle]:
        return self.grain_carts[:self.active_grain_cart_count]

    @property
    def working_combine_count(self) -> int:
        tolerance = self.config.numerical_tolerance
        return sum(
            combine.free_storage_bu > tolerance
            for combine in self.active_combines
        )

    @property
    def combine_tank_bu(self) -> float:
        return sum(vehicle.load_bu for vehicle in self.combines)

    @property
    def grain_cart_inventory_bu(self) -> float:
        return sum(vehicle.load_bu for vehicle in self.grain_carts)

    @property
    def receiving_pit_inventory_bu(self) -> float:
        """The Version 1 pit interface is disabled with zero capacity."""
        return 0.0

    @property
    def wet_bin_inventory_bu(self) -> float:
        return sum(lot.amount_bu for lot in self.wet_bin_lots)

    @property
    def semi_at_field_grain_bu(self) -> float:
        return sum(semi.load_bu for semi in self.semis
                   if semi.state == AT_FIELD)

    @property
    def in_transit_grain_bu(self) -> float:
        return sum(semi.load_bu for semi in self.semis
                   if semi.state == TO_GC)

    @property
    def semi_at_gc_grain_bu(self) -> float:
        return sum(semi.load_bu for semi in self.semis
                   if semi.state in (WAITING_UNLOAD, UNLOADING))

    @property
    def committed_grain_bu(self) -> float:
        return sum(vehicle.load_bu for vehicle in self.vehicles)

    @property
    def unprocessed_grain_bu(self) -> float:
        return self.committed_grain_bu + self.wet_bin_inventory_bu

    @property
    def receiving_pressure(self) -> float:
        active_field_vehicles = (
            self.active_combines + self.active_grain_carts)
        field_capacity = sum(
            vehicle.storage_capacity_bu for vehicle in active_field_vehicles)
        field_capacity += sum(
            semi.storage_capacity_bu for semi in self.semis)
        if field_capacity <= self.config.numerical_tolerance:
            return 1.0
        free = sum(vehicle.free_storage_bu
                   for vehicle in active_field_vehicles)
        free += sum(semi.free_storage_bu for semi in self.semis
                    if semi.state == AT_FIELD)
        return 1.0 - max(0.0, free) / field_capacity

    @property
    def current_dryer_rate_buph(self) -> float:
        unloading = next((semi for semi in self.semis
                          if semi.state == UNLOADING and semi.lots), None)
        queue = self.wet_bin_lots or (unloading.lots if unloading else [])
        if not queue:
            return 0.0
        return self.config.dryer_rate_for_moisture(queue[0].moisture_pct)

    @property
    def next_event_h(self) -> float:
        times = [semi.gc_arrival_h for semi in self.semis
                 if semi.state == TO_GC]
        times += [semi.unload_complete_h for semi in self.semis
                  if semi.state == UNLOADING]
        times += [semi.field_return_h for semi in self.semis
                  if semi.state == RETURNING]
        return min(times, default=inf)

    def set_field_context(self, field_id: str,
                          one_way_travel_min: float) -> None:
        self.current_field_id = field_id
        self.current_travel_h = one_way_travel_min / 60.0
        for vehicle in self.combines + self.grain_carts:
            vehicle.location = field_id

    def set_operating_resources(
            self, active_combine_count: int,
            active_grain_cart_count: int) -> None:
        if not 1 <= active_combine_count <= len(self.combines):
            raise ValueError("Invalid active combine count")
        if not 0 <= active_grain_cart_count <= len(self.grain_carts):
            raise ValueError("Invalid active grain-cart count")
        self.active_combine_count = active_combine_count
        self.active_grain_cart_count = active_grain_cart_count

    def fork_for_forecast(self) -> "Downstream":
        forecast = deepcopy(self)
        forecast.events = []
        forecast.record_events = False
        forecast._radio_eta_signature = None
        forecast._radio_return_schedule = ()
        return forecast

    def active_combine_free_bu(self, active_combine_count: int) -> float:
        return sum(vehicle.free_storage_bu
                   for vehicle in self.combines[:active_combine_count])

    def add_harvested_grain(
            self, grain_bu: float, moisture_pct: float, field_id: str) -> None:
        """Allocate team output to independent active combine tanks.

        Allocation is proportional to each still-working combine's own harvest
        capacity and is redistributed when a tank reaches its limit. The grain
        cart contributes to team throughput but never creates its own grain lot.
        """
        remaining = grain_bu
        tolerance = self.config.numerical_tolerance
        candidates = [vehicle for vehicle in self.active_combines
                      if vehicle.free_storage_bu > tolerance]
        while remaining > tolerance and candidates:
            total_weight = sum(vehicle.harvest_capacity_buph
                               for vehicle in candidates)
            moved = 0.0
            for vehicle in tuple(candidates):
                share = remaining * vehicle.harvest_capacity_buph / total_weight
                accepted = min(share, vehicle.free_storage_bu)
                _append_lot(vehicle.lots, accepted, moisture_pct, field_id)
                moved += accepted
            remaining -= moved
            candidates = [vehicle for vehicle in candidates
                          if vehicle.free_storage_bu > tolerance]
            if moved <= tolerance:
                break
        if remaining > 1e-6:
            raise ValueError("Harvested grain exceeds active combine storage")

    def _dryer_rate(self, moisture_pct: float) -> float:
        return self.config.dryer_rate_for_moisture(moisture_pct)

    def _dry(self, amount_bu: float) -> float:
        if amount_bu <= self.config.numerical_tolerance:
            return 0.0
        dried_lots: list[GrainLot] = []
        _move_lots(self.wet_bin_lots, dried_lots, amount_bu)
        dried_bu = sum(lot.amount_bu for lot in dried_lots)
        for lot in dried_lots:
            self.total_dried_moisture_bushel_pct += (
                lot.amount_bu * lot.moisture_pct)
            self.dried_by_field_bu[lot.field_id] = (
                self.dried_by_field_bu.get(lot.field_id, 0.0)
                + lot.amount_bu)
        return dried_bu

    def _flow_rates(
            self, unloading: Vehicle | None) -> tuple[float, float, float]:
        c = self.config
        wet = self.wet_bin_inventory_bu
        head_queue = self.wet_bin_lots or (
            unloading.lots if unloading else [])
        dryer_rate = (
            self._dryer_rate(head_queue[0].moisture_pct)
            if head_queue else 0.0)
        nominal_unload_rate = (
            c.semi_storage_capacity_bu / c.semi_unload_time_h)
        source_rate = (
            nominal_unload_rate
            if unloading is not None
            and unloading.load_bu > c.numerical_tolerance else 0.0)
        if wet < c.wet_bin_capacity_bu - c.numerical_tolerance:
            receiving_rate = c.grain_leg_rate_buph
        elif dryer_rate > 0:
            # No bypass: this is the rate at which the dryer continuously
            # creates wet-bin space while the bin remains at capacity.
            receiving_rate = min(c.grain_leg_rate_buph, dryer_rate)
        else:
            receiving_rate = 0.0
        unload_rate = min(source_rate, receiving_rate)
        active_dryer_rate = (
            dryer_rate if wet > c.numerical_tolerance
            else min(dryer_rate, unload_rate))
        return unload_rate, unload_rate, active_dryer_rate

    def advance_time(self, duration_h: float) -> None:
        c = self.config
        if (duration_h < 0
                or self.time_h + duration_h
                > self.next_event_h + c.numerical_tolerance):
            raise ValueError("Split the interval at the next downstream event")
        operating_before_h = self.total_dryer_operating_h
        remaining_h = duration_h
        unloading = next((semi for semi in self.semis
                          if semi.state == UNLOADING), None)
        iterations = 0
        while remaining_h > c.numerical_tolerance:
            iterations += 1
            if iterations > 10000:
                raise RuntimeError("Continuous downstream flow did not converge")
            wet = self.wet_bin_inventory_bu
            unload_rate, _, active_dryer_rate = self._flow_rates(unloading)
            if (unload_rate <= c.numerical_tolerance
                    and active_dryer_rate <= c.numerical_tolerance):
                break
            step = remaining_h
            if (unload_rate > c.numerical_tolerance
                    and unloading is not None and unloading.lots):
                step = min(step, unloading.lots[0].amount_bu / unload_rate)
            if active_dryer_rate > c.numerical_tolerance and self.wet_bin_lots:
                step = min(
                    step, self.wet_bin_lots[0].amount_bu / active_dryer_rate)
            wet_net_rate = unload_rate - active_dryer_rate
            if (wet_net_rate > c.numerical_tolerance
                    and wet < c.wet_bin_capacity_bu):
                step = min(
                    step, (c.wet_bin_capacity_bu - wet) / wet_net_rate)
            elif wet_net_rate < -c.numerical_tolerance and wet > 0:
                step = min(step, wet / -wet_net_rate)
            if step <= c.numerical_tolerance:
                step = min(remaining_h, c.numerical_tolerance)

            unload_amount = unload_rate * step
            if unloading is not None:
                moved = _move_lots(
                    unloading.lots, self.wet_bin_lots, unload_amount)
                amount = sum(lot.amount_bu for lot in moved)
                self.total_unloaded_bu += amount
                self.total_leg_transferred_bu += amount
            dried_bu = self._dry(active_dryer_rate * step)
            self.total_dried_bu += dried_bu
            if active_dryer_rate > c.numerical_tolerance:
                self.total_dryer_operating_h += step
            remaining_h -= step

        operating_h = self.total_dryer_operating_h - operating_before_h
        idle_h = max(0.0, duration_h - operating_h)
        if operating_h > c.numerical_tolerance:
            self.dryer_has_started = True
            # Exact transport events split calls to advance_time. Within one
            # event-free interval, a partial operating interval can only end
            # by exhausting available grain, so any idle remainder is the
            # beginning of the next starvation episode.
            self.current_dryer_idle_episode_h = idle_h
        elif self.dryer_has_started:
            self.current_dryer_idle_episode_h += idle_h
        if self.dryer_has_started:
            self.total_dryer_starvation_h += idle_h
            self.maximum_dryer_idle_episode_h = max(
                self.maximum_dryer_idle_episode_h,
                self.current_dryer_idle_episode_h,
            )

        self.time_h += duration_h
        if unloading is not None:
            if unloading.load_bu <= c.numerical_tolerance:
                unloading.lots.clear()
                unloading.unload_complete_h = self.time_h
            else:
                current_rate, _, _ = self._flow_rates(unloading)
                unloading.unload_complete_h = (
                    self.time_h + unloading.load_bu / current_rate
                    if current_rate > c.numerical_tolerance else inf)

    def _moisture_label(self, lots: list[GrainLot]) -> str:
        values = sorted({lot.moisture_pct for lot in lots})
        return "+".join(f"{value:.4f}" for value in values)

    def _field_label(self, lots: list[GrainLot]) -> str:
        values: list[str] = []
        for lot in lots:
            if lot.field_id not in values:
                values.append(lot.field_id)
        return "+".join(values)

    def _event(self, kind: str, semi: Vehicle, grain_bu: float,
               lots: list[GrainLot] | None = None) -> None:
        if not self.record_events:
            return
        event_lots = semi.lots if lots is None else lots
        self.events.append({
            "time_h": self.time_h,
            "event": kind,
            "semi_id": semi.vehicle_id,
            "grain_bu": grain_bu,
            "trip": semi.trips,
            "moisture_pct": self._moisture_label(event_lots),
            "grain_field_ids": self._field_label(event_lots),
            "route_field_id": semi.route_field_id,
            "one_way_travel_min": semi.route_travel_h * 60.0,
        })

    def _depart(self, semi: Vehicle) -> None:
        semi.state = TO_GC
        semi.location = "TO_GC"
        semi.route_travel_h = self.current_travel_h
        semi.route_field_id = self.current_field_id
        semi.gc_arrival_h = self.time_h + semi.route_travel_h
        semi.trips += 1
        self._event("DEPART_FIELD", semi, semi.load_bu)

    def _try_start_unloading(self) -> None:
        c = self.config
        if any(semi.state == UNLOADING for semi in self.semis):
            return
        waiting = sorted(
            (semi for semi in self.semis if semi.state == WAITING_UNLOAD),
            key=lambda semi: (semi.gc_arrival_h, semi.vehicle_id),
        )
        if waiting:
            semi = waiting[0]
            semi.state = UNLOADING
            semi.location = "GRAIN_CENTER"
            semi.unload_initial_bu = semi.load_bu
            semi.unload_manifest = [
                GrainLot(lot.amount_bu, lot.moisture_pct, lot.field_id)
                for lot in semi.lots
            ]
            current_rate, _, _ = self._flow_rates(semi)
            semi.unload_complete_h = (
                self.time_h + semi.load_bu / current_rate
                if current_rate > c.numerical_tolerance else inf)
            self._event("START_UNLOAD", semi, semi.load_bu)

    def handle_events(self) -> None:
        tolerance = self.config.numerical_tolerance
        for semi in self.semis:
            if (semi.state == TO_GC
                    and semi.gc_arrival_h <= self.time_h + tolerance):
                semi.state = WAITING_UNLOAD
                semi.location = "GRAIN_CENTER"
                # Preserve the actual arrival timestamp: the single unload
                # slot is FIFO, including when several semis are already
                # waiting. A later departure overwrites this value.
                self._event("ARRIVE_GC", semi, semi.load_bu)
        for semi in self.semis:
            if (semi.state == UNLOADING
                    and semi.load_bu <= tolerance
                    and semi.unload_complete_h <= self.time_h + tolerance):
                self._event(
                    "FINISH_UNLOAD", semi, semi.unload_initial_bu,
                    semi.unload_manifest)
                semi.state = RETURNING
                semi.location = "RETURNING"
                semi.unload_complete_h = inf
                semi.field_return_h = self.time_h + semi.route_travel_h
                semi.unload_initial_bu = 0.0
                semi.unload_manifest = []
        for semi in self.semis:
            if (semi.state == RETURNING
                    and semi.field_return_h <= self.time_h + tolerance):
                semi.state = AT_FIELD
                semi.location = self.current_field_id
                semi.field_return_h = inf
                self._event("RETURN_FIELD", semi, 0.0, [])
        self._try_start_unloading()

    def _available_semis(self) -> list[Vehicle]:
        return sorted(
            (semi for semi in self.semis if semi.state == AT_FIELD),
            key=lambda semi: (-semi.load_bu, semi.vehicle_id),
        )

    def _fill_semis_from_vehicle(self, source: Vehicle) -> None:
        c = self.config
        for semi in self._available_semis():
            transfer = min(source.load_bu, semi.free_storage_bu)
            if transfer > c.numerical_tolerance:
                moved = _move_lots(source.lots, semi.lots, transfer)
                self._event("LOAD_SEMI", semi, transfer, moved)
            if semi.free_storage_bu <= c.numerical_tolerance:
                self._depart(semi)
            if source.load_bu <= c.numerical_tolerance:
                break

    def _fill_carts_from_combine(
            self, combine: Vehicle,
            carts: Iterable[Vehicle]) -> None:
        tolerance = self.config.numerical_tolerance
        for cart in carts:
            transfer = min(combine.load_bu, cart.free_storage_bu)
            if transfer > tolerance:
                _move_lots(combine.lots, cart.lots, transfer)
            if combine.load_bu <= tolerance:
                break

    def unload_field_storage(
            self, active_combine_count: int,
            active_grain_cart_count: int,
            force: bool = False) -> None:
        del force  # Field transfer is instantaneous in Version 1.
        self.set_operating_resources(
            active_combine_count, active_grain_cart_count)

        # Existing cart grain has first claim on available semi space.
        for cart in self.grain_carts:
            self._fill_semis_from_vehicle(cart)
        # Combines then unload directly to semis.
        for combine in self.combines:
            self._fill_semis_from_vehicle(combine)
        # Active carts are the optional shared field buffer.
        for combine in self.combines:
            self._fill_carts_from_combine(
                combine, self.active_grain_carts)
        # A cart may have become full while a partially loaded semi remains.
        for cart in self.grain_carts:
            self._fill_semis_from_vehicle(cart)

    def dispatch_partial_loads(
            self, active_combine_count: int | None = None,
            active_grain_cart_count: int | None = None) -> None:
        combines = (
            self.active_combine_count
            if active_combine_count is None else active_combine_count)
        carts = (
            self.active_grain_cart_count
            if active_grain_cart_count is None else active_grain_cart_count)
        self.unload_field_storage(combines, carts, force=True)
        for semi in self.semis:
            if (semi.state == AT_FIELD
                    and semi.load_bu > self.config.numerical_tolerance):
                self._depart(semi)

    def field_mobile_free_bu(
            self, active_combine_count: int,
            active_grain_cart_count: int) -> float:
        free = sum(vehicle.free_storage_bu
                   for vehicle in self.combines[:active_combine_count])
        free += sum(vehicle.free_storage_bu
                    for vehicle in self.grain_carts[:active_grain_cart_count])
        free += sum(semi.free_storage_bu for semi in self.semis
                    if semi.state == AT_FIELD)
        return free

    def known_semi_return_relief(
            self, horizon_h: float) -> tuple[tuple[float, float], ...]:
        """Return currently committed semi relief as ``(ETA, free bu)``.

        This forecast creates no future harvest, loads, or trips. It only
        advances trips that already exist at the decision time, matching the
        ETA information that can be communicated by radio.
        """
        c = self.config
        if horizon_h <= 0:
            return ()
        def time_key(value: float) -> float:
            return value if value == inf else round(value, 9)

        signature = tuple(
            (
                semi.vehicle_id,
                semi.state,
                time_key(semi.gc_arrival_h),
                time_key(semi.unload_complete_h),
                time_key(semi.field_return_h),
                round(semi.route_travel_h, 9),
                (round(semi.load_bu, 6)
                 if semi.state in (TO_GC, WAITING_UNLOAD) else 0.0),
            )
            for semi in self.semis
            if semi.state != AT_FIELD
        )
        if signature == self._radio_eta_signature:
            return tuple(
                (absolute_h - self.time_h, capacity_bu)
                for absolute_h, capacity_bu in self._radio_return_schedule
                if (self.time_h - c.numerical_tolerance
                    <= absolute_h
                    <= self.time_h + horizon_h + c.numerical_tolerance)
            )
        forecast = self.fork_for_forecast()
        start_h = forecast.time_h
        pending = {
            semi.vehicle_id
            for semi in forecast.semis
            if semi.state != AT_FIELD
        }
        absolute_schedule: list[tuple[float, float]] = []
        while pending:
            forecast.handle_events()
            for semi in forecast.semis:
                if semi.vehicle_id in pending and semi.state == AT_FIELD:
                    absolute_schedule.append((
                        forecast.time_h,
                        semi.free_storage_bu,
                    ))
                    pending.remove(semi.vehicle_id)
            if not pending:
                break
            next_h = forecast.next_event_h
            if next_h == inf:
                break
            duration_h = next_h - forecast.time_h
            if duration_h <= c.numerical_tolerance:
                raise RuntimeError("Committed semi ETA did not advance")
            forecast.advance_time(duration_h)
        self._radio_eta_signature = signature
        self._radio_return_schedule = tuple(sorted(absolute_schedule))
        return tuple(
            (absolute_h - start_h, capacity_bu)
            for absolute_h, capacity_bu in self._radio_return_schedule
            if absolute_h <= start_h + horizon_h + c.numerical_tolerance
        )

    def _weighted_moisture_pct(self, lots: list[GrainLot]) -> float:
        total_bu = sum(lot.amount_bu for lot in lots)
        if total_bu <= self.config.numerical_tolerance:
            return 0.0
        return sum(lot.amount_bu * lot.moisture_pct
                   for lot in lots) / total_bu

    def snapshot(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "combine_tank_bu": self.combine_tank_bu,
            "grain_cart_inventory_bu": self.grain_cart_inventory_bu,
            "semi_at_field_grain_bu": self.semi_at_field_grain_bu,
            "field_grain_bu": (
                self.combine_tank_bu + self.grain_cart_inventory_bu
                + self.semi_at_field_grain_bu),
            "in_transit_grain_bu": self.in_transit_grain_bu,
            "semi_at_gc_grain_bu": self.semi_at_gc_grain_bu,
            "receiving_pit_inventory_bu": 0.0,
            "wet_bin_inventory_bu": self.wet_bin_inventory_bu,
            "wet_bin_head_moisture_pct": (
                self.wet_bin_lots[0].moisture_pct
                if self.wet_bin_lots else 0.0),
            "wet_bin_average_moisture_pct": self._weighted_moisture_pct(
                self.wet_bin_lots),
            "dryer_head_moisture_pct": (
                self.wet_bin_lots[0].moisture_pct
                if self.wet_bin_lots else 0.0),
            "dryer_rate_buph": self.current_dryer_rate_buph,
            "semis_at_field": sum(
                semi.state == AT_FIELD for semi in self.semis),
            "semis_waiting_at_gc": sum(
                semi.state == WAITING_UNLOAD for semi in self.semis),
            "receiving_pressure": self.receiving_pressure,
            "committed_grain_bu": self.committed_grain_bu,
            "cumulative_grain_unloaded_bu": self.total_unloaded_bu,
            "cumulative_grain_to_wet_bin_bu": self.total_leg_transferred_bu,
            "cumulative_grain_dried_bu": self.total_dried_bu,
            "cumulative_dryer_operating_h": self.total_dryer_operating_h,
            "current_dryer_idle_episode_h": (
                self.current_dryer_idle_episode_h),
            "maximum_dryer_idle_episode_h": (
                self.maximum_dryer_idle_episode_h),
            "cumulative_dryer_starvation_h": self.total_dryer_starvation_h,
            "cumulative_dried_average_moisture_pct": (
                self.total_dried_moisture_bushel_pct / self.total_dried_bu
                if self.total_dried_bu > self.config.numerical_tolerance
                else 0.0),
            "grain_center_inventory_bu": self.wet_bin_inventory_bu,
            "cumulative_grain_received_bu": self.total_unloaded_bu,
            "cumulative_grain_processed_bu": self.total_dried_bu,
        }
        for index, combine in enumerate(self.combines, start=1):
            row[f"combine_{index}_state"] = combine.state
            row[f"combine_{index}_load_bu"] = combine.load_bu
        for index, cart in enumerate(self.grain_carts, start=1):
            row[f"grain_cart_{index}_state"] = cart.state
            row[f"grain_cart_{index}_load_bu"] = cart.load_bu
        for index, semi in enumerate(self.semis, start=1):
            row[f"semi_{index}_state"] = semi.state
            row[f"semi_{index}_load_bu"] = semi.load_bu
            row[f"semi_{index}_location"] = semi.location
        return row
