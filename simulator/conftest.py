"""Pytest root for the simulator.

Its presence puts simulator/ on sys.path (pytest's default "prepend" import
mode), so `pytest` run from simulator/ can import the `sim` package. If Track 4's
pyproject.toml sets `pythonpath = ["."]` for pytest, this file can be removed.
"""
