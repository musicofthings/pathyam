"""Safety and correctness of the quantity-expression evaluator.

The safety tests matter more than the arithmetic ones: qty_expr comes from a
database table that nutritionists edit, so every one of these strings is a thing
someone could actually put in the system.
"""

from __future__ import annotations

import numpy as np
import pytest

from pathyam_engine.expressions import (
    ExpressionError, evaluate, referenced_names, validate,
)

N = 100


@pytest.fixture
def vars_():
    rng = np.random.default_rng(7)
    return {
        "batter_g": rng.normal(90, 10, N),
        "rice_fraction": np.full(N, 0.75),
        "fat_g": rng.lognormal(2.08, 0.4, N),
        "rice_type": np.array(["parboiled"] * 60 + ["raw"] * 40, dtype=object),
        "fat_type": np.array(["gingelly"] * 50 + ["ghee"] * 50, dtype=object),
    }


# ------------------------------------------------------------------ safety ----

@pytest.mark.parametrize("expr", [
    "__import__('os').system('rm -rf /')",
    "().__class__.__bases__[0].__subclasses__()",
    "open('/etc/passwd').read()",
    "batter_g.__class__",
    "eval('1+1')",
    "exec('x=1')",
    "[x for x in range(10)]",
    "lambda: 1",
    "{'a': 1}",
    "batter_g[0]",
    "globals()",
    "print(batter_g)",
    "batter_g if (y := 3) else 0",
])
def test_rejects_unsafe_expressions(expr, vars_):
    with pytest.raises(ExpressionError):
        evaluate(expr, vars_, n_samples=N)


def test_rejects_unknown_function():
    with pytest.raises(ExpressionError, match="not permitted"):
        evaluate("sum(batter_g)", {"batter_g": np.ones(N)}, n_samples=N)


def test_rejects_keyword_arguments():
    with pytest.raises(ExpressionError, match="keyword arguments"):
        evaluate("round(batter_g, decimals=2)", {"batter_g": np.ones(N)}, n_samples=N)


def test_rejects_unknown_variable(vars_):
    with pytest.raises(ExpressionError, match="unknown variable"):
        evaluate("batter_g * mystery_param", vars_, n_samples=N)


def test_rejects_overlong_expression():
    with pytest.raises(ExpressionError, match="exceeds"):
        evaluate("1 + " * 200 + "1", {}, n_samples=N)


def test_rejects_empty_expression():
    with pytest.raises(ExpressionError, match="empty"):
        evaluate("   ", {}, n_samples=N)


def test_rejects_syntax_error():
    with pytest.raises(ExpressionError, match="syntax error"):
        evaluate("batter_g *", {}, n_samples=N)


# ------------------------------------------------------------- correctness ----

def test_basic_arithmetic(vars_):
    out = evaluate("batter_g * rice_fraction", vars_, n_samples=N)
    np.testing.assert_allclose(out, vars_["batter_g"] * 0.75)


def test_categorical_selector_idiom(vars_):
    """(rice_type == "parboiled") must act as a 0/1 multiplier."""
    out = evaluate('batter_g * (rice_type == "parboiled")', vars_, n_samples=N)
    expected = vars_["batter_g"] * (vars_["rice_type"] == "parboiled")
    np.testing.assert_allclose(out.astype(float), expected.astype(float))
    assert np.count_nonzero(out) == 60


def test_selectors_partition_the_mass(vars_):
    """The parboiled and raw branches must together equal the whole, never double-count."""
    a = evaluate('batter_g * (rice_type == "parboiled")', vars_, n_samples=N).astype(float)
    b = evaluate('batter_g * (rice_type == "raw")', vars_, n_samples=N).astype(float)
    np.testing.assert_allclose(a + b, vars_["batter_g"])


def test_whitelisted_functions(vars_):
    np.testing.assert_allclose(
        evaluate("min(fat_g, 5.0)", vars_, n_samples=N), np.minimum(vars_["fat_g"], 5.0)
    )
    np.testing.assert_allclose(
        evaluate("clip(fat_g, 2.0, 10.0)", vars_, n_samples=N),
        np.clip(vars_["fat_g"], 2.0, 10.0),
    )


def test_conditional_expression(vars_):
    out = evaluate('batter_g if rice_fraction > 0.5 else 0.0', vars_, n_samples=N)
    np.testing.assert_allclose(out, vars_["batter_g"])


def test_chained_comparison():
    v = {"x": np.array([1.0, 5.0, 9.0])}
    out = evaluate("2 < x < 8", v, n_samples=3)
    np.testing.assert_array_equal(out, [False, True, False])


def test_boolean_operators():
    v = {"a": np.array([1.0, 0.0, 1.0]), "b": np.array([1.0, 1.0, 0.0])}
    np.testing.assert_array_equal(
        evaluate("(a > 0) and (b > 0)", v, n_samples=3), [True, False, False]
    )
    np.testing.assert_array_equal(
        evaluate("(a > 0) or (b > 0)", v, n_samples=3), [True, True, True]
    )


def test_constant_expression_is_broadcast():
    out = evaluate("42.0", {}, n_samples=N)
    assert out.shape == (N,)
    assert np.all(out == 42.0)


def test_non_finite_result_raises_with_context():
    """Division by a parameter that samples to zero must fail loudly, not produce NaN."""
    v = {"a": np.ones(5), "b": np.array([1.0, 2.0, 0.0, 4.0, 5.0])}
    with pytest.raises(ExpressionError, match="non-finite"):
        evaluate("a / b", v, n_samples=5)


# ---------------------------------------------------------------- validate ----

def test_validate_accepts_declared_names():
    validate("batter_g * (1 - rice_fraction) * 0.95", {"batter_g", "rice_fraction"})


def test_validate_rejects_typo():
    with pytest.raises(ExpressionError, match="undeclared parameter"):
        validate("batter_g * rice_fracton", {"batter_g", "rice_fraction"})


def test_validate_ignores_function_names():
    validate("min(fat_g, 5)", {"fat_g"})


def test_referenced_names():
    assert referenced_names('a * b + max(c, 2)') == {"a", "b", "c"}
