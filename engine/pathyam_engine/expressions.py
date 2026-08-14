"""Safe, vectorised evaluation of recipe quantity expressions.

Template authors write arithmetic over declared parameters, stored as text in
``ref.template_ingredient.qty_expr``::

    batter_g * rice_fraction * (rice_type == "parboiled")
    fat_g * (fat_type == "ghee")
    (potato_g + onion_g) * 0.010

These strings come from a database that non-engineers edit. They are therefore
untrusted input, and ``eval()`` is not an option: it would hand any nutritionist
with write access to one table full code execution inside the compute service.

This module walks the AST and evaluates only an explicit whitelist of node types.
Anything else - attribute access, subscripts, lambdas, comprehensions, imports,
walrus, f-strings - raises before evaluation. There is no ``__builtins__`` in
scope because nothing is ever passed to ``eval`` or ``exec``.

Evaluation is vectorised: every variable is a NumPy array of length ``n_samples``,
so one pass evaluates the whole Monte Carlo batch. Boolean comparisons yield
boolean arrays, which multiply as 0/1 - that is what makes the
``(rice_type == "parboiled")`` selector idiom work.
"""

from __future__ import annotations

import ast
from functools import lru_cache
import operator
from typing import Any, Iterable, Mapping

import numpy as np

__all__ = ["ExpressionError", "evaluate", "validate", "referenced_names"]


class ExpressionError(ValueError):
    """Raised for a malformed, unsafe, or non-evaluable quantity expression."""

    def __init__(self, message: str, expression: str | None = None) -> None:
        self.expression = expression
        super().__init__(message if expression is None else f"{message}  [expr: {expression!r}]")


# --------------------------------------------------------------------------
# Whitelists. Adding to these is a security decision, not a convenience one.
# --------------------------------------------------------------------------

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: np.logical_not,
}

_COMPARISONS = {
    ast.Eq: lambda a, b: np.equal(a, b),
    ast.NotEq: lambda a, b: np.not_equal(a, b),
    ast.Lt: lambda a, b: np.less(a, b),
    ast.LtE: lambda a, b: np.less_equal(a, b),
    ast.Gt: lambda a, b: np.greater(a, b),
    ast.GtE: lambda a, b: np.greater_equal(a, b),
}

# Deliberately small. Every entry is elementwise and total - no reductions, so an
# expression cannot collapse the sample axis and silently break the Monte Carlo.
_FUNCTIONS = {
    "min": np.minimum,
    "max": np.maximum,
    "abs": np.abs,
    "clip": np.clip,
    "where": np.where,
    "sqrt": np.sqrt,
    "exp": np.exp,
    "log": np.log,
    "round": np.round,
    "floor": np.floor,
    "ceil": np.ceil,
}

_MAX_EXPRESSION_LENGTH = 500
_MAX_AST_NODES = 200


def referenced_names(expression: str) -> set[str]:
    """Return the variable names an expression reads.

    Used at template-load time to check that every name is a declared parameter,
    so a typo fails when the template is saved rather than when a user logs a meal.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - message varies by version
        raise ExpressionError(f"syntax error: {exc.msg}", expression) from exc
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in _FUNCTIONS
    }


def validate(expression: str, allowed_names: Iterable[str]) -> None:
    """Check an expression is safe and references only declared parameters.

    Raises :class:`ExpressionError` on any violation. Call this on every
    ``qty_expr`` when loading or saving a template.
    """
    tree = _parse(expression)
    _check_nodes(tree, expression)

    allowed = set(allowed_names)
    unknown = referenced_names(expression) - allowed
    if unknown:
        raise ExpressionError(
            f"undeclared parameter(s) {sorted(unknown)}; "
            f"template declares {sorted(allowed)}",
            expression,
        )


def evaluate(
    expression: str,
    variables: Mapping[str, Any],
    *,
    n_samples: int | None = None,
) -> np.ndarray:
    """Evaluate ``expression`` against ``variables``, returning a NumPy array.

    Every value in ``variables`` should be an array of length ``n_samples`` (scalars
    are broadcast). The result is always an array so callers never have to branch
    on whether an expression happened to be constant.
    """
    tree = _parse(expression)
    _check_nodes(tree, expression)

    # NumPy emits warnings for div-by-zero and invalid ops. Suppress them here and
    # convert the resulting non-finite values into a loud, contextual error below:
    # a silent NaN propagating into a nutrient total is the failure mode to avoid.
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        result = _eval_node(tree.body, variables, expression)

    result = np.asarray(result)
    if result.ndim == 0 and n_samples is not None:
        result = np.full(n_samples, result.item())

    if result.dtype.kind in "fc" and not np.all(np.isfinite(result)):
        bad = int(np.count_nonzero(~np.isfinite(result)))
        raise ExpressionError(
            f"produced {bad} non-finite value(s) - usually division by a parameter "
            f"that can sample to zero, or an overflow in a power term",
            expression,
        )
    return result


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------


@lru_cache(maxsize=1024)
def _parse(expression: str) -> ast.Expression:
    if not isinstance(expression, str):
        raise ExpressionError(f"expression must be str, got {type(expression).__name__}")
    if not expression.strip():
        raise ExpressionError("empty expression")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ExpressionError(
            f"expression exceeds {_MAX_EXPRESSION_LENGTH} characters "
            f"({len(expression)}); split it into intermediate parameters"
        )
    try:
        return ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"syntax error: {exc.msg}", expression) from exc


def _check_nodes(tree: ast.Expression, expression: str) -> None:
    """Reject any AST node type not on the whitelist, before evaluating anything."""
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_AST_NODES:
        raise ExpressionError(
            f"expression too complex ({len(nodes)} AST nodes, max {_MAX_AST_NODES})",
            expression,
        )

    for node in nodes:
        if isinstance(node, (ast.Expression, ast.Name, ast.Constant, ast.Load,
                             ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
                             ast.IfExp, ast.And, ast.Or)):
            continue
        if isinstance(node, tuple(_BINOPS)) or isinstance(node, tuple(_UNARYOPS)):
            continue
        if isinstance(node, tuple(_COMPARISONS)):
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError("only direct calls to whitelisted functions", expression)
            if node.func.id not in _FUNCTIONS:
                raise ExpressionError(
                    f"function {node.func.id!r} not permitted; "
                    f"allowed: {sorted(_FUNCTIONS)}",
                    expression,
                )
            if node.keywords:
                raise ExpressionError("keyword arguments are not permitted", expression)
            continue
        raise ExpressionError(
            f"{type(node).__name__} is not permitted in quantity expressions",
            expression,
        )


def _eval_node(node: ast.AST, variables: Mapping[str, Any], expr: str) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool, str)):
            return node.value
        raise ExpressionError(f"constant of type {type(node.value).__name__} not permitted", expr)

    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        raise ExpressionError(
            f"unknown variable {node.id!r}; available: {sorted(variables)}", expr
        )

    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"operator {type(node.op).__name__} not permitted", expr)
        return op(_eval_node(node.left, variables, expr),
                  _eval_node(node.right, variables, expr))

    if isinstance(node, ast.UnaryOp):
        op = _UNARYOPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"unary {type(node.op).__name__} not permitted", expr)
        return op(_eval_node(node.operand, variables, expr))

    if isinstance(node, ast.Compare):
        # Chained comparisons (a < b < c) combine elementwise with logical_and.
        left = _eval_node(node.left, variables, expr)
        result = None
        for op_node, comparator in zip(node.ops, node.comparators):
            fn = _COMPARISONS.get(type(op_node))
            if fn is None:
                raise ExpressionError(
                    f"comparison {type(op_node).__name__} not permitted", expr
                )
            right = _eval_node(comparator, variables, expr)
            piece = fn(left, right)
            result = piece if result is None else np.logical_and(result, piece)
            left = right
        return result

    if isinstance(node, ast.BoolOp):
        # Python's `and`/`or` short-circuit on truthiness, which is wrong for arrays.
        combine = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
        values = [_eval_node(v, variables, expr) for v in node.values]
        result = values[0]
        for value in values[1:]:
            result = combine(result, value)
        return result

    if isinstance(node, ast.IfExp):
        return np.where(
            _eval_node(node.test, variables, expr),
            _eval_node(node.body, variables, expr),
            _eval_node(node.orelse, variables, expr),
        )

    if isinstance(node, ast.Call):
        fn = _FUNCTIONS[node.func.id]  # membership already checked in _check_nodes
        args = [_eval_node(a, variables, expr) for a in node.args]
        try:
            return fn(*args)
        except TypeError as exc:
            raise ExpressionError(
                f"{node.func.id}() called with {len(args)} argument(s): {exc}", expr
            ) from exc

    raise ExpressionError(f"{type(node).__name__} is not permitted", expr)
