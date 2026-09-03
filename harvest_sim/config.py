from dataclasses import dataclass
from math import isfinite


HIGH = "High"
LOW = "Low"


@dataclass(frozen=True)
class FieldSpec:
    field_id: str
    area_ac: float
    geometry: str
    moisture: str
    one_way_travel_min: float


DEFAULT_FIELDS = (
    FieldSpec("F1", 50.0, "irregular", HIGH, 5.0),
    FieldSpec("F2", 80.0, "rectangular", HIGH, 15.0),
    FieldSpec("F3", 50.0, "irregular", LOW, 20.0),
    FieldSpec("F4", 80.0, "rectangular", LOW, 5.0),
    FieldSpec("F5", 50.0, "irregular", LOW, 15.0),
    FieldSpec("F6", 80.0, "rectangular", LOW, 20.0),
)


@dataclass(frozen=True)
class SimulationConfig:
    field_specs: tuple[FieldSpec, ...] = DEFAULT_FIELDS

    # Each field has a 10-acre opening; its remaining area is interior harvest.
    opening_area_ac: float = 10.0
    interior_capacity_acph: float = 7.5
    rectangular_opening_capacity_acph: float = 6.634615384615384
    irregular_opening_capacity_acph: float = 5.048076923076923
    header_width_ft: float = 30.0
    yield_bu_per_ac: float = 200.0

    # Mobile storage and transport.
    combine_tank_capacity_bu: float = 500.0
    semi_count: int = 1
    semi_capacity_bu: float = 1000.0
    semi_unload_time_h: float = 10.0 / 60.0

    # A one-truck receiving pit decouples fixed unloading time from the grain leg.
    receiving_pit_capacity_bu: float = 1000.0
    grain_leg_rate_buph: float = 3000.0
    wet_bin_capacity_bu: float = 10000.0
    dryer_high_moisture_rate_buph: float = 500.0
    dryer_low_moisture_rate_buph: float = 800.0

    dt_h: float = 1.0 / 60.0
    switch_time_h: float = 20.0 / 60.0
    high_receiving_pressure_fraction: float = 0.80
    low_receiving_pressure_fraction: float = 0.40
    numerical_tolerance: float = 1e-8
    max_simulation_h: float = 1000.0

    def __post_init__(self) -> None:
        positive = (
            "interior_capacity_acph", "rectangular_opening_capacity_acph",
            "irregular_opening_capacity_acph", "yield_bu_per_ac", "dt_h",
            "combine_tank_capacity_bu", "semi_capacity_bu", "semi_unload_time_h",
            "receiving_pit_capacity_bu", "grain_leg_rate_buph", "wet_bin_capacity_bu",
            "dryer_high_moisture_rate_buph", "dryer_low_moisture_rate_buph",
            "numerical_tolerance", "max_simulation_h",
        )
        for name in positive:
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not isfinite(self.opening_area_ac) or self.opening_area_ac < 0:
            raise ValueError("opening_area_ac must be finite and nonnegative")
        if not isfinite(self.switch_time_h) or self.switch_time_h < 0:
            raise ValueError("switch_time_h must be finite and nonnegative")
        if not isinstance(self.semi_count, int) or isinstance(self.semi_count, bool) or self.semi_count < 1:
            raise ValueError("semi_count must be a positive integer")
        if self.receiving_pit_capacity_bu + self.numerical_tolerance < self.semi_capacity_bu:
            raise ValueError("receiving pit must hold at least one full semi")
        if not 0 <= self.low_receiving_pressure_fraction < self.high_receiving_pressure_fraction <= 1:
            raise ValueError("Receiving pressure thresholds must satisfy 0 <= low < high <= 1")
        ids = [field.field_id for field in self.field_specs]
        if len(ids) != len(set(ids)):
            raise ValueError("field_id values must be unique")
        for field in self.field_specs:
            if field.geometry not in ("rectangular", "irregular"):
                raise ValueError("Field geometry must be rectangular or irregular")
            if field.moisture not in (HIGH, LOW):
                raise ValueError("Field moisture must be High or Low")
            if not isfinite(field.area_ac) or field.area_ac < self.opening_area_ac:
                raise ValueError("Each field area must be at least the opening area")
            if not isfinite(field.one_way_travel_min) or field.one_way_travel_min < 0:
                raise ValueError("Field travel time must be finite and nonnegative")

    @property
    def total_area_ac(self) -> float:
        return sum(field.area_ac for field in self.field_specs)

    @property
    def total_grain_bu(self) -> float:
        return self.total_area_ac * self.yield_bu_per_ac
