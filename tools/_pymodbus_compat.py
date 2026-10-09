"""Compatibility helpers for running the legacy pymodbus benchmark."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from inspect import signature

_UNIT_KEYWORDS = ("device_id", "slave")
_BASELINE_METHODS = ("read_holding_registers", "write_register")


def _supported_unit_keyword(method: Callable[..., object], method_name: str) -> str:
    try:
        parameters = signature(method).parameters
    except (TypeError, ValueError) as err:
        raise RuntimeError(f"Cannot inspect pymodbus {method_name} signature") from err

    for keyword in _UNIT_KEYWORDS:
        if keyword in parameters:
            return keyword
    raise RuntimeError(
        f"Unsupported pymodbus {method_name} signature: expected a "
        "'device_id' or 'slave' parameter"
    )


def _adapt_unit_keyword(
    method: Callable[..., object], method_name: str, target_keyword: str
) -> Callable[..., object]:
    @wraps(method)
    def compatible(self: object, *args: object, **kwargs: object) -> object:
        supplied = [keyword for keyword in _UNIT_KEYWORDS if keyword in kwargs]
        if len(supplied) == 2:
            if kwargs["device_id"] != kwargs["slave"]:
                raise TypeError(
                    f"Pass only one unit address to pymodbus {method_name}"
                )
            kwargs.pop(next(keyword for keyword in supplied if keyword != target_keyword))
        elif supplied and supplied[0] != target_keyword:
            kwargs[target_keyword] = kwargs.pop(supplied[0])
        return method(self, *args, **kwargs)

    return compatible


def install_pymodbus_unit_keyword_compat(client_class: type[object]) -> None:
    """Allow either pymodbus unit keyword while preserving its installed API."""

    methods = []
    for method_name in _BASELINE_METHODS:
        method = getattr(client_class, method_name)
        target_keyword = _supported_unit_keyword(method, method_name)
        methods.append(
            (
                method_name,
                _adapt_unit_keyword(method, method_name, target_keyword),
            )
        )

    for method_name, compatible_method in methods:
        setattr(client_class, method_name, compatible_method)
