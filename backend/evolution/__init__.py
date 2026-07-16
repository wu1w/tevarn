"""Public exports for Takton Evolution Engine."""

from backend.evolution.config import get_evolution_config, set_evolution_config
from backend.evolution.manager import EvolutionManager, get_evolution_manager

__all__ = [
    "EvolutionManager",
    "get_evolution_manager",
    "get_evolution_config",
    "set_evolution_config",
]
