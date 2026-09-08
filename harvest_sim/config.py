from dataclasses import dataclass
from math import isfinite
from random import Random


MODE_1C = "1C"
MODE_2C = "2C"
MODE_2C_1GC = "2C+1GC"
INTERIOR_MODES = (MODE_1C, MODE_2C, MODE_2C_1GC)
MIN_INTERIOR_PREDICTION_HORIZON_H = 12.0


@dataclass(frozen=True)
class FieldSpec:
    field_id: str
    area_ac: float
    geometry: str
    one_way_travel_min: float


DEFAULT_FIELDS = (
    FieldSpec("F1", 50.0, "irregular", 5.0),
    FieldSpec("F2", 80.0, "rectangular", 15.0),
    FieldSpec("F3", 50.0, "irregular", 20.0),
    FieldSpec("F4", 80.0, "rectangular", 5.0),
    FieldSpec("F5", 50.0, "irregular", 15.0),
    FieldSpec("F6", 80.0, "rectangular", 20.0),
    FieldSpec("F7", 50.0, "irregular", 15.0),
    FieldSpec("F8", 80.0, "rectangular", 15.0),
    FieldSpec("F9", 50.0, "irregular", 15.0),
    FieldSpec("F10", 80.0, "rectangular", 15.0),
)


@dataclass(frozen=True)
class SimulationConfig:
    field_specs: tuple[FieldSpec, ...] = DEFAULT_FIELDS

    # Opening is a fixed 2C operation with a separate effective rate.  The
    # provisional rates preserve the previous rectangular/irregular field-
    # efficiency ratios relative to the nominal 2C interior rate.
    opening_area_ac: float = 10.0
    rectangular_opening_2c_rate_buph: float = 3538.4615384615386
    irregular_opening_2c_rate_buph: float = 2692.3076923076924

    # Effective in-field rates already include turning, unloading, normal
    # delays, tank cycles, and grain-handling interaction.
    interior_1c_rate_buph: float = 2000.0
    interior_2c_rate_buph: float = 4000.0
    interior_2c_1gc_rate_buph: float = 5000.0
    legacy_interior_rate_buph: float = 1500.0
    max_interior_mode_switches: int = 2
    minimum_interior_mode_dwell_h: float = 30.0 / 60.0
    interior_mode_switch_time_h: float = 0.0
    interior_prediction_horizon_h: float = MIN_INTERIOR_PREDICTION_HORIZON_H
    predictive_blocking_penalty: float = 0.05
    rest_inventory_shortage_weight: float = 1.0
    rest_coverage_transport_fraction: float = 0.65
    harvest_work_window_h: float = 14.0
    harvest_rest_window_h: float = 10.0
    header_width_ft: float = 30.0
    yield_bu_per_ac: float = 200.0

    # Mobile storage and transport.
    combine_tank_capacity_bu: float = 500.0
    semi_count: int = 1
    semi_capacity_bu: float = 1000.0
    semi_unload_time_h: float = 10.0 / 60.0

    # The pit is a finite buffer. Semi unloading, grain-leg transfer, and drying
    # occur concurrently; downstream backpressure may extend unloading time.
    receiving_pit_capacity_bu: float = 1000.0
    grain_leg_rate_buph: float = 3000.0
    wet_bin_capacity_bu: float = 10000.0

    # Moisture is sampled once per field, then remains fixed for the scenario.
    random_seed: int = 42
    moisture_min_pct: float = 18.0
    moisture_max_pct: float = 22.0
    dryer_intercept_buph: float = 2600.0
    dryer_moisture_slope_buph_per_pct: float = 100.0

    dt_h: float = 1.0 / 60.0
    switch_time_h: float = 20.0 / 60.0
    high_receiving_pressure_fraction: float = 0.80
    low_receiving_pressure_fraction: float = 0.40
    numerical_tolerance: float = 1e-8
    max_simulation_h: float = 1000.0

    def __post_init__(self) -> None:
        positive = (
            "rectangular_opening_2c_rate_buph", "irregular_opening_2c_rate_buph",
            "interior_1c_rate_buph", "interior_2c_rate_buph",
            "interior_2c_1gc_rate_buph", "legacy_interior_rate_buph",
            "interior_prediction_horizon_h", "harvest_work_window_h",
            "yield_bu_per_ac", "dt_h",
            "combine_tank_capacity_bu", "semi_capacity_bu", "semi_unload_time_h",
            "receiving_pit_capacity_bu", "grain_leg_rate_buph", "wet_bin_capacity_bu",
            "dryer_intercept_buph", "dryer_moisture_slope_buph_per_pct",
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
        if (not isfinite(self.minimum_interior_mode_dwell_h)
                or self.minimum_interior_mode_dwell_h < 0):
            raise ValueError("minimum_interior_mode_dwell_h must be finite and nonnegative")
        if (not isfinite(self.interior_mode_switch_time_h)
                or self.interior_mode_switch_time_h < 0):
            raise ValueError("interior_mode_switch_time_h must be finite and nonnegative")
        if (not isfinite(self.predictive_blocking_penalty)
                or self.predictive_blocking_penalty < 0):
            raise ValueError("predictive_blocking_penalty must be finite and nonnegative")
        if (not isfinite(self.rest_inventory_shortage_weight)
                or self.rest_inventory_shortage_weight < 0):
            raise ValueError("rest_inventory_shortage_weight must be finite and nonnegative")
        if (not isfinite(self.rest_coverage_transport_fraction)
                or not 0 < self.rest_coverage_transport_fraction <= 1):
            raise ValueError(
                "rest_coverage_transport_fraction must be in (0, 1]")
        if self.interior_prediction_horizon_h < MIN_INTERIOR_PREDICTION_HORIZON_H:
            raise ValueError(
                "interior_prediction_horizon_h must be at least "
                f"{MIN_INTERIOR_PREDICTION_HORIZON_H:g} hours"
            )
        if (not isfinite(self.harvest_rest_window_h)
                or self.harvest_rest_window_h < 0):
            raise ValueError("harvest_rest_window_h must be finite and nonnegative")
        if not isinstance(self.semi_count, int) or isinstance(self.semi_count, bool) or self.semi_count < 1:
            raise ValueError("semi_count must be a positive integer")
        if (not isinstance(self.max_interior_mode_switches, int)
                or isinstance(self.max_interior_mode_switches, bool)
                or self.max_interior_mode_switches < 0):
            raise ValueError("max_interior_mode_switches must be a nonnegative integer")
        if not (self.interior_1c_rate_buph < self.interior_2c_rate_buph
                < self.interior_2c_1gc_rate_buph):
            raise ValueError("Interior rates must satisfy 1C < 2C < 2C+1GC")
        if not isinstance(self.random_seed, int) or isinstance(self.random_seed, bool):
            raise ValueError("random_seed must be an integer")
        if (not isfinite(self.moisture_min_pct)
                or not isfinite(self.moisture_max_pct)
                or self.moisture_min_pct >= self.moisture_max_pct):
            raise ValueError("moisture bounds must be finite and satisfy min < max")
        if self.dryer_rate_for_moisture(self.moisture_max_pct) <= 0:
            raise ValueError("dryer throughput must remain positive over the moisture range")
        if not 0 <= self.low_receiving_pressure_fraction < self.high_receiving_pressure_fraction <= 1:
            raise ValueError("Receiving pressure thresholds must satisfy 0 <= low < high <= 1")
        ids = [field.field_id for field in self.field_specs]
        if len(ids) != len(set(ids)):
            raise ValueError("field_id values must be unique")
        for field in self.field_specs:
            if field.geometry not in ("rectangular", "irregular"):
                raise ValueError("Field geometry must be rectangular or irregular")
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

    def sample_field_moistures(self) -> tuple[float, ...]:
        """Return one reproducible U(min, max) moisture draw per field."""
        rng = Random(self.random_seed)
        return tuple(rng.uniform(self.moisture_min_pct, self.moisture_max_pct)
                     for _ in self.field_specs)

    def dryer_rate_for_moisture(self, moisture_pct: float) -> float:
        """Linear calibration: D(M) = intercept - slope * M."""
        return (self.dryer_intercept_buph
                - self.dryer_moisture_slope_buph_per_pct * moisture_pct)

    def rest_dryer_inventory_target_bu(self, dryer_rate_buph: float) -> float:
        """Wet-bin inventory that lets the active dryer run through one rest."""
        if dryer_rate_buph <= self.numerical_tolerance:
            return 0.0
        return min(self.wet_bin_capacity_bu,
                   dryer_rate_buph * self.harvest_rest_window_h)

    def opening_rate_buph(self, geometry: str) -> float:
        return (self.rectangular_opening_2c_rate_buph
                if geometry == "rectangular"
                else self.irregular_opening_2c_rate_buph)

    def interior_rate_buph(self, mode: str) -> float:
        rates = {
            MODE_1C: self.interior_1c_rate_buph,
            MODE_2C: self.interior_2c_rate_buph,
            MODE_2C_1GC: self.interior_2c_1gc_rate_buph,
        }
        try:
            return rates[mode]
        except KeyError as error:
            raise ValueError(f"Unknown interior mode: {mode}") from error
