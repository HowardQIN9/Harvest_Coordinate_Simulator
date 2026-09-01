from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class SimulationConfig:
    field_geometries: tuple[str, ...] = (
        "rectangular", "rectangular", "irregular",
        "rectangular", "irregular", "irregular",
    )

    # field config
    opening_area_ac: float = 10.0
    interior_area_ac: float = 40.0
    # Capacities are independent; throughput is capacity * yield_bu_per_ac.
    interior_capacity_acph: float = 7.5
    rectangular_opening_capacity_acph: float = 6.634615384615384
    irregular_opening_capacity_acph: float = 5.048076923076923
    header_width_ft: float = 30.0

    yield_bu_per_ac: float = 200.0



    # No ground storage. All semis start empty at the field.
    combine_tank_capacity_bu: float = 500.0
    semi_count: int = 1
    semi_capacity_bu: float = 1000.0
    semi_gc_arrival_h: float = 20.0 / 60.0
    semi_round_trip_h: float = 45.0 / 60.0
    grain_center_processing_rate_buph: float = 1000.0

    #time config
    dt_h: float = 1.0 / 60.0
    switch_time_h: float = 20.0 / 60.0

    # Pressure = 1 - available receiving space / total mobile capacity.
    high_receiving_pressure_fraction: float = 0.80
    low_receiving_pressure_fraction: float = 0.40

    numerical_tolerance: float = 1e-8
    max_simulation_h: float = 1000.0

    def __post_init__(self) -> None:
        positive = (
            "interior_capacity_acph", "rectangular_opening_capacity_acph",
            "irregular_opening_capacity_acph", "yield_bu_per_ac", "dt_h",
            "combine_tank_capacity_bu", "semi_capacity_bu", "semi_gc_arrival_h",
            "semi_round_trip_h", "grain_center_processing_rate_buph",
            "numerical_tolerance", "max_simulation_h",
        )
        for name in positive:
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("opening_area_ac", "interior_area_ac", "switch_time_h"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if not isinstance(self.semi_count, int) or isinstance(self.semi_count, bool) or self.semi_count < 1:
            raise ValueError("semi_count must be a positive integer")
        if self.semi_round_trip_h < self.semi_gc_arrival_h:
            raise ValueError("semi_round_trip_h must include the trip to the grain center")
        if not 0 <= self.low_receiving_pressure_fraction < self.high_receiving_pressure_fraction <= 1:
            raise ValueError("Receiving pressure thresholds must satisfy 0 <= low < high <= 1")
        if any(geometry not in ("rectangular", "irregular") for geometry in self.field_geometries):
            raise ValueError("Field geometry must be rectangular or irregular")

    @property
    def total_area_ac(self) -> float:
        return len(self.field_geometries) * (self.opening_area_ac + self.interior_area_ac)

    @property
    def total_grain_bu(self) -> float:
        return self.total_area_ac * self.yield_bu_per_ac
