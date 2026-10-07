"""Safe arithmetic: percentage-point vs relative change, zero denominators,
unit compatibility, operation whitelist."""
import pytest

from services.calculation_service import (CalculationError, Operand,
                                          percentage_change,
                                          percentage_point_change, ratio,
                                          run)


def test_percentage_point_vs_relative_change():
    """72% -> 84%: +12 percentage points AND ~+16.67% relative. Distinct."""
    old, new = Operand(72, unit="%", period="Q2"), Operand(84, unit="%", period="Q4")
    pp = percentage_point_change(old, new)
    rel = percentage_change(old, new)
    assert pp.value == 12.0 and pp.unit == "percentage points"
    assert rel.value == pytest.approx(16.6667, abs=1e-3) and rel.unit == "%"


def test_zero_denominator_rejected():
    with pytest.raises(CalculationError, match="zero"):
        percentage_change(Operand(0, unit="%"), Operand(50, unit="%"))
    with pytest.raises(CalculationError, match="zero"):
        ratio(Operand(10), Operand(0))


def test_incompatible_units_rejected():
    with pytest.raises(CalculationError, match="Incompatible units"):
        percentage_change(Operand(72, unit="%"), Operand(84, unit="USD"))


def test_percentage_point_requires_percent_units():
    with pytest.raises(CalculationError, match="percentages"):
        percentage_point_change(Operand(72, unit="units"), Operand(84, unit="units"))


def test_unknown_operation_rejected():
    with pytest.raises(CalculationError, match="Unknown operation"):
        run("__import__('os').system('dir')", [Operand(1)])
    with pytest.raises(CalculationError, match="Unknown operation"):
        run("eval", [Operand(1)])


def test_approximate_chart_inputs_noted():
    result = percentage_change(
        Operand(72, unit="%", approximate=True), Operand(84, unit="%"))
    assert any("approximate" in n for n in result.notes)
