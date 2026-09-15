"""Use Sailfish phone sensors through the local Sailfish Sensors Bridge."""

from .client import SensorClient, SensorError

__all__ = ["SensorClient", "SensorError"]
