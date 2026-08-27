from dataclasses import dataclass


@dataclass(frozen=True)
class SimulationConfig:
    field_geometries: tuple[str, ...] = (
        "rectangular", "rectangular", "irregular",
        "rectangular", "irregular", "rectangular",
    )
    opening_area_ac: float = 10.0
    interior_area_ac: float = 40.0
    # Interior throughput = 1,500 bu/h at 200 bu/ac. Opening capacities retain
    # the original 4.6:3.5:5.2 proportional relationship.
    interior_capacity_acph: float = 7.5
    rectangular_opening_capacity_acph: float = 6.634615384615384
    irregular_opening_capacity_acph: float = 5.048076923076923
    header_width_ft: float = 30.0
    yield_bu_per_ac: float = 200.0
    buffer_capacity_bu: float = 1000.0
    downstream_rate_buph: float = 1000.0
    dt_h: float = 1.0 / 60.0
    switch_time_h: float = 20.0 / 60.0
    high_buffer_fraction: float = 0.80
    low_buffer_fraction: float = 0.40
    adaptive_min_task_h: float = 1.0
    numerical_tolerance: float = 1e-8
    max_simulation_h: float = 1000.0

    @property
    def total_area_ac(self) -> float:
        return len(self.field_geometries) * (self.opening_area_ac + self.interior_area_ac)

    @property
    def total_grain_bu(self) -> float:
        return self.total_area_ac * self.yield_bu_per_ac
