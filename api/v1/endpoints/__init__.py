# -*- coding: utf-8 -*-
"""
===================================
API v1 Endpoints 模块初始化
===================================

职责：
1. 懒加载所有 endpoint 路由模块
"""

from importlib import import_module

_ENDPOINT_MODULES = {
    "health",
    "analysis",
    "history",
    "stocks",
    "backtest",
    "system_config",
    "auth",
    "agent",
    "usage",
    "portfolio",
    "alerts",
    "decision_signals",
    "alphasift",
    "intelligence",
    "commercial_analysis",
}

__all__ = sorted(_ENDPOINT_MODULES)


def __getattr__(name: str):
    if name not in _ENDPOINT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module
