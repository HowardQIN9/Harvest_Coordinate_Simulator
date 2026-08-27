from dataclasses import dataclass


@dataclass
class Downstream:
    capacity_bu: float
    processing_rate_buph: float
    buffer_bu: float = 0.0
    total_processed_bu: float = 0.0

    def process(self, duration_h: float) -> float:
        processed = min(self.buffer_bu, self.processing_rate_buph * duration_h)
        self.buffer_bu -= processed
        self.total_processed_bu += processed
        return processed

    def accept(self, grain_bu: float) -> float:
        accepted = min(grain_bu, self.capacity_bu - self.buffer_bu)
        self.buffer_bu += accepted
        return accepted

    def step_with_inflow(self, potential_grain_bu: float, duration_h: float) -> tuple[float, float]:
        """Process concurrent inflow while preserving mass and the buffer bound.

        Returns ``(accepted_grain, processed_grain)``. Grain that cannot enter the
        system is not discarded; the simulator converts it to blocked combine time.
        """
        available = self.buffer_bu + potential_grain_bu
        processed = min(available, self.processing_rate_buph * duration_h)
        unconstrained_buffer = available - processed

        if unconstrained_buffer <= self.capacity_bu:
            accepted = potential_grain_bu
            self.buffer_bu = unconstrained_buffer
        else:
            excess = unconstrained_buffer - self.capacity_bu
            accepted = potential_grain_bu - excess
            self.buffer_bu = self.capacity_bu

        self.total_processed_bu += processed
        return accepted, processed
