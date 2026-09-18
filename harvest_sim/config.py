from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from random import Random


MODE_1C = "1C"
MODE_2C = "2C"
MODE_2C_1GC = "2C+1GC"
MODE_STOP = "STOP"

OPENING_MODES = (MODE_1C, MODE_2C)
INTERIOR_MODES = (MODE_1C, MODE_2C, MODE_2C_1GC)
MIN_PREDICTION_HORIZON_H = 8.0


@dataclass(frozen=True)
class OperatingMode:
    """A field-team configuration expressed as required vehicle counts."""

    name: str
    n_combines: int
    n_grain_carts: int


OPERATING_MODES = {
    MODE_1C: OperatingMode(MODE_1C, 1, 0),
    MODE_2C: OperatingMode(MODE_2C, 2, 0),
    MODE_2C_1GC: OperatingMode(MODE_2C_1GC, 2, 1),
}


@dataclass(frozen=True)
class FieldSpec:
    """Manual/generator input used to construct a fresh mutable Field."""

    field_id: str
    area_ac: float
    geometry: str
    one_way_travel_min: float
    opening_area_fraction: float | None = None
    opening_capacity_factor: float | None = None
    yield_bu_per_ac: float | None = None
    moisture_pct: float | None = None


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
    fixed_field_order_ids: tuple[str, ...] = ()
    default_field_order_rule: str = "far_to_near"

    # FieldGenerator defaults. FieldSpec values override these per field.
    opening_area_fraction: float = 0.15
    rectangular_opening_capacity_factor: float = 3538.4615384615386 / 4000.0
    irregular_opening_capacity_factor: float = 2692.3076923076924 / 4000.0
    yield_bu_per_ac: float = 200.0
    moisture_min_pct: float = 18.0
    moisture_max_pct: float = 22.0
    random_seed: int = 42

    # Fleet counts N and per-vehicle properties.
    n_combines: int = 2
    n_grain_carts: int = 1
    n_semis: int = 3
    combine_harvest_capacity_buph: float = 1600.0
    grain_cart_harvest_capacity_buph: float = 800.0
    combine_storage_capacity_bu: float = 300.0
    grain_cart_storage_capacity_bu: float = 1000.0
    semi_storage_capacity_bu: float = 1000.0

    max_interior_mode_switches: int = 2
    minimum_interior_mode_dwell_h: float = 30.0 / 60.0
    interior_mode_switch_time_h: float = 5.0 / 60.0
    field_switch_time_h: float = 20.0 / 60.0

    # STOP eligibility = HARVESTING + FORCED_BLOCKED + MODE_SWITCHING.
    stop_work_requirement_h: float = 6.0
    minimum_stop_h: float = 6.0
    stop_signal_confirmation_h: float = 30.0 / 60.0
    maximum_dryer_idle_episode_h: float = 1.0
    forced_block_ignore_h: float = 0.1
    planned_stop_extension_options_h: tuple[float, ...] = (0.5, 1.0)
    harvest_completion_tolerance_h: float = 30.0 / 60.0
    minimum_field_engaged_reduction_h: float = 5.0

    prediction_horizon_h: float = 48.0
    predictive_decision_interval_h: float = 30.0 / 60.0
    predictive_beam_width: int = 50
    forecast_step_h: float = 15.0 / 60.0
    control_decision_interval_h: float = 1.0 / 60.0
    snapshot_interval_h: float = 5.0 / 60.0
    reactive_mode_horizon_h: float = 1.0

    low_pressure_fraction: float = 0.40
    high_pressure_fraction: float = 0.80
    stop_pressure_fraction: float = 0.95

    header_width_ft: float = 30.0
    semi_unload_time_h: float = 20.0 / 60.0
    receiving_pit_capacity_bu: float = 0.0
    grain_leg_rate_buph: float = 3000.0
    wet_bin_capacity_bu: float = 10000.0
    dryer_intercept_buph: float = 2600.0
    dryer_moisture_slope_buph_per_pct: float = 100.0

    dt_h: float = 1.0 / 60.0
    numerical_tolerance: float = 1e-8
    max_simulation_h: float = 1500.0

    def __post_init__(self) -> None:
        positive = (
            "yield_bu_per_ac", "combine_harvest_capacity_buph",
            "grain_cart_harvest_capacity_buph",
            "combine_storage_capacity_bu", "grain_cart_storage_capacity_bu",
            "semi_storage_capacity_bu", "minimum_interior_mode_dwell_h",
            "stop_work_requirement_h", "minimum_stop_h",
            "stop_signal_confirmation_h", "maximum_dryer_idle_episode_h",
            "prediction_horizon_h",
            "predictive_decision_interval_h", "forecast_step_h",
            "control_decision_interval_h", "snapshot_interval_h",
            "reactive_mode_horizon_h",
            "semi_unload_time_h", "grain_leg_rate_buph",
            "wet_bin_capacity_bu", "dryer_intercept_buph",
            "dryer_moisture_slope_buph_per_pct", "dt_h",
            "numerical_tolerance", "max_simulation_h",
        )
        for name in positive:
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in (
                "interior_mode_switch_time_h", "field_switch_time_h",
                "harvest_completion_tolerance_h",
                "minimum_field_engaged_reduction_h", "forced_block_ignore_h"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.receiving_pit_capacity_bu != 0.0:
            raise ValueError(
                "Version 1 reserves the receiving-pit interface but requires "
                "receiving_pit_capacity_bu = 0")
        for name in ("n_combines", "n_grain_carts", "n_semis"):
            value = getattr(self, name)
            minimum = 0 if name == "n_grain_carts" else 1
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < minimum):
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if (not isinstance(self.max_interior_mode_switches, int)
                or isinstance(self.max_interior_mode_switches, bool)
                or self.max_interior_mode_switches < 0):
            raise ValueError(
                "max_interior_mode_switches must be a nonnegative integer")
        if not 0 < self.opening_area_fraction < 1:
            raise ValueError("opening_area_fraction must be in (0, 1)")
        for name in (
                "rectangular_opening_capacity_factor",
                "irregular_opening_capacity_factor"):
            value = getattr(self, name)
            if not isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.prediction_horizon_h < MIN_PREDICTION_HORIZON_H:
            raise ValueError(
                f"prediction_horizon_h must be at least "
                f"{MIN_PREDICTION_HORIZON_H:g}")
        if (not isinstance(self.predictive_beam_width, int)
                or isinstance(self.predictive_beam_width, bool)
                or self.predictive_beam_width < 1):
            raise ValueError("predictive_beam_width must be a positive integer")
        if (not self.planned_stop_extension_options_h
                or any(not isfinite(value) or value <= 0
                       for value in self.planned_stop_extension_options_h)):
            raise ValueError(
                "planned_stop_extension_options_h must contain finite "
                "positive values")
        if not 0 <= self.low_pressure_fraction < self.high_pressure_fraction < self.stop_pressure_fraction <= 1:
            raise ValueError(
                "Pressure thresholds must satisfy 0 <= low < high < stop <= 1")
        if self.default_field_order_rule not in (
                "far_to_near", "near_to_far", "listed"):
            raise ValueError("Unknown default_field_order_rule")
        if not isinstance(self.random_seed, int) or isinstance(
                self.random_seed, bool):
            raise ValueError("random_seed must be an integer")
        if (not isfinite(self.moisture_min_pct)
                or not isfinite(self.moisture_max_pct)
                or self.moisture_min_pct >= self.moisture_max_pct):
            raise ValueError(
                "moisture bounds must be finite and satisfy min < max")
        if self.dryer_rate_for_moisture(self.moisture_max_pct) <= 0:
            raise ValueError(
                "dryer throughput must remain positive over the moisture range")

        if not self.field_specs:
            raise ValueError("field_specs must contain at least one field")
        ids = [field.field_id for field in self.field_specs]
        if len(ids) != len(set(ids)):
            raise ValueError("field_id values must be unique")
        for field in self.field_specs:
            if field.geometry not in ("rectangular", "irregular"):
                raise ValueError(
                    "Field geometry must be rectangular or irregular")
            if not isfinite(field.area_ac) or field.area_ac <= 0:
                raise ValueError("Each field area must be finite and positive")
            if (not isfinite(field.one_way_travel_min)
                    or field.one_way_travel_min < 0):
                raise ValueError(
                    "Field travel time must be finite and nonnegative")
            self._validate_optional_field_values(field)
        if self.fixed_field_order_ids:
            if (len(self.fixed_field_order_ids) != len(ids)
                    or set(self.fixed_field_order_ids) != set(ids)):
                raise ValueError(
                    "fixed_field_order_ids must contain every field id "
                    "exactly once")
        if not self.feasible_modes(OPENING_MODES):
            raise ValueError("Fleet cannot support any Opening mode")
        if not self.feasible_modes(INTERIOR_MODES):
            raise ValueError("Fleet cannot support any Interior mode")

    def _validate_optional_field_values(self, field: FieldSpec) -> None:
        if (field.opening_area_fraction is not None
                and not 0 < field.opening_area_fraction < 1):
            raise ValueError("Field opening_area_fraction must be in (0, 1)")
        if (field.opening_capacity_factor is not None
                and not 0 < field.opening_capacity_factor <= 1):
            raise ValueError(
                "Field opening_capacity_factor must be in (0, 1]")
        if (field.yield_bu_per_ac is not None
                and (not isfinite(field.yield_bu_per_ac)
                     or field.yield_bu_per_ac <= 0)):
            raise ValueError(
                "Field yield_bu_per_ac must be finite and positive")
        if (field.moisture_pct is not None
                and (not isfinite(field.moisture_pct)
                     or not self.moisture_min_pct <= field.moisture_pct
                     <= self.moisture_max_pct)):
            raise ValueError(
                "Field moisture_pct must lie within the configured bounds")

    @property
    def total_area_ac(self) -> float:
        return sum(field.area_ac for field in self.field_specs)

    @property
    def total_grain_bu(self) -> float:
        return sum(
            field.area_ac
            * (self.yield_bu_per_ac
               if field.yield_bu_per_ac is None else field.yield_bu_per_ac)
            for field in self.field_specs
        )

    @property
    def maximum_combine_storage_bu(self) -> float:
        return self.n_combines * self.combine_storage_capacity_bu

    @property
    def total_grain_cart_storage_bu(self) -> float:
        return self.n_grain_carts * self.grain_cart_storage_capacity_bu

    @property
    def total_system_storage_bu(self) -> float:
        return (
            self.maximum_combine_storage_bu
            + self.total_grain_cart_storage_bu
            + self.n_semis * self.semi_storage_capacity_bu
            + self.receiving_pit_capacity_bu
            + self.wet_bin_capacity_bu
        )

    # Compatibility read-only names used by plotting/result code.
    @property
    def semi_count(self) -> int:
        return self.n_semis

    @property
    def semi_capacity_bu(self) -> float:
        return self.semi_storage_capacity_bu

    @property
    def combine_tank_capacity_per_combine_bu(self) -> float:
        return self.combine_storage_capacity_bu

    @property
    def grain_cart_capacity_bu(self) -> float:
        return self.grain_cart_storage_capacity_bu

    @property
    def interior_1c_rate_buph(self) -> float:
        return self.interior_rate_buph(MODE_1C)

    @property
    def interior_2c_rate_buph(self) -> float:
        return self.interior_rate_buph(MODE_2C)

    @property
    def interior_2c_1gc_rate_buph(self) -> float:
        return self.interior_rate_buph(MODE_2C_1GC)

    @property
    def rectangular_opening_rate_fraction(self) -> float:
        return self.rectangular_opening_capacity_factor

    @property
    def irregular_opening_rate_fraction(self) -> float:
        return self.irregular_opening_capacity_factor

    def field_order_indices(self) -> tuple[int, ...]:
        if self.fixed_field_order_ids:
            index_by_id = {
                field.field_id: index
                for index, field in enumerate(self.field_specs)
            }
            return tuple(
                index_by_id[field_id] for field_id in self.fixed_field_order_ids)
        if self.default_field_order_rule == "listed":
            return tuple(range(len(self.field_specs)))
        reverse = self.default_field_order_rule == "far_to_near"
        return tuple(sorted(
            range(len(self.field_specs)),
            key=lambda index: (
                -self.field_specs[index].one_way_travel_min
                if reverse else self.field_specs[index].one_way_travel_min,
                index,
            ),
        ))

    def sample_field_moistures(self) -> tuple[float, ...]:
        rng = Random(self.random_seed)
        return tuple(
            field.moisture_pct
            if field.moisture_pct is not None
            else rng.uniform(self.moisture_min_pct, self.moisture_max_pct)
            for field in self.field_specs
        )

    def dryer_rate_for_moisture(self, moisture_pct: float) -> float:
        return (
            self.dryer_intercept_buph
            - self.dryer_moisture_slope_buph_per_pct * moisture_pct
        )

    def opening_area_ac(
            self, field_area_ac: float,
            opening_fraction: float | None = None) -> float:
        fraction = (
            self.opening_area_fraction
            if opening_fraction is None else opening_fraction
        )
        return fraction * field_area_ac

    def mode(self, mode: str) -> OperatingMode:
        try:
            return OPERATING_MODES[mode]
        except KeyError as error:
            raise ValueError(f"Unknown operating mode: {mode}") from error

    def mode_is_feasible(self, mode: str) -> bool:
        definition = self.mode(mode)
        return (
            definition.n_combines <= self.n_combines
            and definition.n_grain_carts <= self.n_grain_carts
        )

    def feasible_modes(self, modes: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(mode for mode in modes if self.mode_is_feasible(mode))

    def opening_capacity_factor(self, geometry: str) -> float:
        return (
            self.rectangular_opening_capacity_factor
            if geometry == "rectangular"
            else self.irregular_opening_capacity_factor
        )

    def opening_rate_buph(
            self, geometry: str, mode: str,
            opening_capacity_factor: float | None = None) -> float:
        if mode not in OPENING_MODES:
            raise ValueError(f"Opening does not allow mode {mode}")
        factor = (
            self.opening_capacity_factor(geometry)
            if opening_capacity_factor is None else opening_capacity_factor
        )
        return self.interior_rate_buph(mode) * factor

    def interior_rate_buph(self, mode: str) -> float:
        definition = self.mode(mode)
        return (
            definition.n_combines * self.combine_harvest_capacity_buph
            + definition.n_grain_carts
            * self.grain_cart_harvest_capacity_buph
        )

    def combine_count_for_mode(self, mode: str) -> int:
        return self.mode(mode).n_combines

    def grain_cart_count_for_mode(self, mode: str) -> int:
        return self.mode(mode).n_grain_carts

    def grain_cart_enabled_for_mode(self, mode: str) -> bool:
        return self.grain_cart_count_for_mode(mode) > 0
