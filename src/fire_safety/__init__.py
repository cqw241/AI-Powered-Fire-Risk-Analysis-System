"""Fire risk analysis application package."""

from pathlib import Path

__version__ = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

__all__ = ["PROJECT_ROOT", "__version__"]
