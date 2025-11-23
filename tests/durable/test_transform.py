import ast

from durable import (
    DurableAstTransformer,
    DurableProgram,
    make_durable,
    transform_to_durable_ast,
)


def test_make_durable_wraps_function():
    async def sample():
        return 1

    program = make_durable(sample)

    assert isinstance(program, DurableProgram)
    assert program.fn is sample


def test_transform_to_durable_ast_strips_params_and_adds_globals():
    async def with_args(a, b):
        return a + b

    transformed = transform_to_durable_ast(with_args)
    func_def = transformed.body[0]

    assert isinstance(func_def, ast.AsyncFunctionDef)
    assert func_def.args.args == []
    assert isinstance(func_def.body[0], ast.Global)
    assert "call_stack" in func_def.body[0].names
    assert "checkpoint" in func_def.body[0].names


def test_durable_ast_transformer_handles_if_blocks():
    source = """
async def fn(x):
    if x:
        return x
    return 0
"""
    tree = ast.parse(source)
    transformer = DurableAstTransformer()
    transformed = transformer.visit(tree)

    assert isinstance(transformed.body[0], ast.AsyncFunctionDef)
    # Should insert global statement and marker assignment
    names = [node for node in transformed.body[0].body if isinstance(node, ast.Global)]
    assert names
