"""Simulator control for playground sites (contract §11.4): the /api/sim routes, forwarded to the
simulator's own control API (T4-9)."""

from .routes import add_sim_api

__all__ = ["add_sim_api"]
