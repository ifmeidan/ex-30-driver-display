"""Display configuration for the Waveshare 8.8" 1920x480 panel."""

from dataclasses import dataclass


@dataclass
class DisplayConfig:
    """Resolution and rendering settings."""
    width: int = 480
    height: int = 1920
    fps: int = 60
    fullscreen: bool = True
