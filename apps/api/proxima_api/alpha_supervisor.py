"""One-release import compatibility for the former Alpha supervisor."""
from .master_supervisor import MasterSupervisor as AlphaSupervisor

__all__ = ["AlphaSupervisor"]
