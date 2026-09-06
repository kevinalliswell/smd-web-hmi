"""Explicitly configured, loopback-only software device; never a physical STM32 driver."""

from .fixtures import SimulatorPairing, synthetic_profile, synthetic_recipe
from .server import V2Simulator

__all__ = ["SimulatorPairing", "V2Simulator", "synthetic_profile", "synthetic_recipe"]
