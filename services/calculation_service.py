"""Controlled arithmetic for evidence-backed calculations.

The model may *request* a calculation by naming an operation and operands
(each operand tied to an evidence element), but the arithmetic itself always
runs here in Python. Model-generated code is never executed and ``eval`` is
never used.

Key distinctions enforced:
- percentage_change: relative % change, (b-a)/a*100
- percentage_point_change: simple difference of two percentages, b-a
- Zero denominators and incompatible units raise CalculationError instead of
  returning a plausible-looking wrong number.
"""
from dataclasses import dataclass, field
from typing import Optional


class CalculationError(Exception):
    pass


@dataclass
class Operand:
    value: float
    unit: Optional[str] = None          # "%", "units/hour", "USD", ...
    period: Optional[str] = None        # "Q2 2025", ...
    evidence_id: Optional[str] = None   # element the value came from
    approximate: bool = False           # True when read off a chart


@dataclass
class CalculationResult:
    operation: str
    value: float
    unit: Optional[str]
    formula: str
    inputs: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _check_units(a: Operand, b: Operand) -> None:
    if a.unit and b.unit and a.unit != b.unit:
        raise CalculationError(
            f"Incompatible units: '{a.unit}' vs '{b.unit}'. Convert first or "
            "report both values separately.")


def percentage_change(old: Operand, new: Operand) -> CalculationResult:
    _check_units(old, new)
    if old.value == 0:
        raise CalculationError(
            "Cannot compute a relative percentage change from a zero baseline; "
            "report the absolute change instead.")
    value = (new.value - old.value) / abs(old.value) * 100.0
    notes = []
    if old.approximate or new.approximate:
        notes.append("one or more inputs were read from a chart and are approximate")
    return CalculationResult(
        operation="percentage_change", value=round(value, 4), unit="%",
        formula=f"({new.value} - {old.value}) / |{old.value}| * 100",
        inputs=[old, new], notes=notes)


def percentage_point_change(old: Operand, new: Operand) -> CalculationResult:
    _check_units(old, new)
    for op in (old, new):
        if op.unit and op.unit.strip() != "%":
            raise CalculationError(
                "percentage_point_change requires both operands to be percentages "
                f"(got unit '{op.unit}').")
    value = new.value - old.value
    return CalculationResult(
        operation="percentage_point_change", value=round(value, 4),
        unit="percentage points",
        formula=f"{new.value} - {old.value}",
        inputs=[old, new],
        notes=["this is an absolute difference of percentages, not a relative change"])


def difference(a: Operand, b: Operand) -> CalculationResult:
    _check_units(a, b)
    return CalculationResult(
        operation="difference", value=round(b.value - a.value, 6),
        unit=b.unit or a.unit, formula=f"{b.value} - {a.value}", inputs=[a, b])


def ratio(a: Operand, b: Operand) -> CalculationResult:
    if b.value == 0:
        raise CalculationError("Division by zero: denominator value is 0.")
    return CalculationResult(
        operation="ratio", value=round(a.value / b.value, 6), unit=None,
        formula=f"{a.value} / {b.value}", inputs=[a, b])


def total(operands: list[Operand]) -> CalculationResult:
    if not operands:
        raise CalculationError("total() requires at least one operand.")
    units = {o.unit for o in operands if o.unit}
    if len(units) > 1:
        raise CalculationError(f"Incompatible units across operands: {sorted(units)}")
    value = sum(o.value for o in operands)
    return CalculationResult(
        operation="total", value=round(value, 6),
        unit=units.pop() if units else None,
        formula=" + ".join(str(o.value) for o in operands), inputs=operands)


OPERATIONS = {
    "percentage_change": lambda ops: percentage_change(ops[0], ops[1]),
    "percentage_point_change": lambda ops: percentage_point_change(ops[0], ops[1]),
    "difference": lambda ops: difference(ops[0], ops[1]),
    "ratio": lambda ops: ratio(ops[0], ops[1]),
    "total": lambda ops: total(ops),
}


def run(operation: str, operands: list[Operand]) -> CalculationResult:
    """Execute a whitelisted operation; anything else is rejected."""
    fn = OPERATIONS.get(operation)
    if fn is None:
        raise CalculationError(
            f"Unknown operation '{operation}'. Allowed: {sorted(OPERATIONS)}")
    arity2 = operation in ("percentage_change", "percentage_point_change",
                           "difference", "ratio")
    if arity2 and len(operands) != 2:
        raise CalculationError(f"'{operation}' requires exactly 2 operands.")
    return fn(operands)
