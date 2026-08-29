"""Signals, structures, pricing, sizing and the engine that sequences them.

Intentionally empty of re-exports: `strategy.base` depends on `risk.portfolio`, and a
convenience import here would close that loop into a circular import. Import from the
concrete module instead, which also makes the dependency direction obvious at the
call site.
"""
