from __future__ import annotations

from typing import Protocol


class TransportForecast(Protocol):
    """Boundary used by control rollouts to simulate physical grain movement.

    The implementation currently lives in ``Downstream`` because transport,
    receiving, and drying share one event clock.  Keeping the controller
    coupled only to this protocol makes it possible to move the vehicle/event
    implementation into ``transport.py`` later without changing the control
    algorithm.
    """

    time_h: float
    total_dryer_operating_h: float

    @property
    def next_event_h(self) -> float: ...

    @property
    def unprocessed_grain_bu(self) -> float: ...

    @property
    def committed_grain_bu(self) -> float: ...

    @property
    def wet_bin_inventory_bu(self) -> float: ...

    @property
    def receiving_pressure(self) -> float: ...

    @property
    def current_dryer_rate_buph(self) -> float: ...

    @property
    def current_dryer_idle_episode_h(self) -> float: ...

    @property
    def working_combine_count(self) -> int: ...

    def fork_for_forecast(self) -> "TransportForecast": ...

    def set_field_context(
            self, field_id: str, one_way_travel_min: float) -> None: ...

    def handle_events(self) -> None: ...

    def advance_time(self, duration_h: float) -> None: ...

    def add_harvested_grain(
            self, grain_bu: float, moisture_pct: float,
            field_id: str) -> None: ...

    def active_combine_free_bu(
            self, active_combine_count: int) -> float: ...

    def unload_field_storage(
            self, active_combine_count: int,
            active_grain_cart_count: int, force: bool = False) -> None: ...

    def dispatch_partial_loads(
            self, active_combine_count: int | None = None,
            active_grain_cart_count: int | None = None) -> None: ...

    def field_mobile_free_bu(
            self, active_combine_count: int,
            active_grain_cart_count: int) -> float: ...

    def known_semi_return_relief(
            self, horizon_h: float) -> tuple[tuple[float, float], ...]: ...

    @property
    def combine_tank_bu(self) -> float: ...

    @property
    def grain_cart_inventory_bu(self) -> float: ...

    @property
    def semi_at_field_grain_bu(self) -> float: ...
