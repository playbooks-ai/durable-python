"""AST transformation logic to make async functions durable."""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Callable

from .exceptions import (
    AlreadyDurableException,
    NoSourceCodeException,
)

# Cache for memoizing AST transformations
_durable_cache: dict[int, ast.AST] = {}


def transform_to_durable_ast(fn: Callable):
    """Return a transformed AST for the durable version of ``fn``."""
    fn_id = id(fn)
    if fn_id in _durable_cache:
        return _durable_cache[fn_id]

    if hasattr(fn, "__durable_transformed__"):
        raise AlreadyDurableException()

    try:
        source = inspect.getsource(fn)
        source = textwrap.dedent(source)
    except (OSError, TypeError):
        raise NoSourceCodeException()

    parsed = ast.parse(source)

    transformer = DurableAstTransformer()
    transformed = transformer.visit(parsed)
    ast.fix_missing_locations(transformed)

    _durable_cache[fn_id] = transformed

    return transformed


class DurableAstTransformer(ast.NodeTransformer):
    """Transforms async function AST to add checkpoint guards and durability."""

    def __init__(self):
        self.checkpoint_counter = 0
        self.in_async_function = False
        self.local_vars = set()
        self.param_vars = set()
        self.loop_counter = 0
        self.loop_offset_stack = []

    def visit_AsyncFunctionDef(self, node):
        old_in_async = self.in_async_function
        self.in_async_function = True

        old_counter = self.checkpoint_counter
        self.checkpoint_counter = 0

        old_local_vars = self.local_vars
        old_param_vars = self.param_vars
        self.local_vars = set()
        self.param_vars = set()

        self.collect_param_vars(node)
        self.collect_local_vars(node)

        global_names = (
            ["call_stack", "checkpoint"]
            + sorted(list(self.param_vars))
            + sorted(list(self.local_vars))
        )
        global_stmt = ast.Global(names=global_names)

        marker_stmt = ast.Assign(
            targets=[ast.Name(id="__durable_transformed__", ctx=ast.Store())],
            value=ast.Constant(value=True),
        )

        new_body = [global_stmt, marker_stmt]

        for stmt in node.body:
            if isinstance(stmt, ast.Global):
                continue
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "__durable_transformed__"
            ):
                continue
            new_body.extend(self.visit_statement(stmt))

        node.body = new_body

        node.args = ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        )

        node.decorator_list = []

        self.in_async_function = old_in_async
        self.checkpoint_counter = old_counter
        self.local_vars = old_local_vars
        self.param_vars = old_param_vars

        return node

    def collect_param_vars(self, node):
        """Collect all parameter names from the function signature"""
        if not isinstance(node, ast.AsyncFunctionDef):
            return

        args = node.args

        for arg in args.args:
            self.param_vars.add(arg.arg)

        for arg in args.posonlyargs:
            self.param_vars.add(arg.arg)

        for arg in args.kwonlyargs:
            self.param_vars.add(arg.arg)

        if args.vararg:
            self.param_vars.add(args.vararg.arg)

        if args.kwarg:
            self.param_vars.add(args.kwarg.arg)

    def collect_local_vars(self, node):
        """Collect all local variable names from the function"""

        class VarCollector(ast.NodeVisitor):
            def __init__(self, transformer):
                self.transformer = transformer

            def visit_Assign(self, node):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id not in self.transformer.param_vars:
                            self.transformer.local_vars.add(target.id)
                self.generic_visit(node)

            def visit_AnnAssign(self, node):
                if isinstance(node.target, ast.Name):
                    if node.target.id not in self.transformer.param_vars:
                        self.transformer.local_vars.add(node.target.id)
                if node.value:
                    self.visit(node.value)

            def visit_AugAssign(self, node):
                if isinstance(node.target, ast.Name):
                    if node.target.id not in self.transformer.param_vars:
                        self.transformer.local_vars.add(node.target.id)
                self.generic_visit(node)

            def visit_For(self, node):
                if isinstance(node.target, ast.Name):
                    if node.target.id not in self.transformer.param_vars:
                        self.transformer.local_vars.add(node.target.id)
                elif isinstance(node.target, ast.Tuple):
                    for elt in node.target.elts:
                        if isinstance(elt, ast.Name):
                            if elt.id not in self.transformer.param_vars:
                                self.transformer.local_vars.add(elt.id)
                self.generic_visit(node)

            def visit_comprehension(self, node):
                if isinstance(node.target, ast.Name):
                    if node.target.id not in self.transformer.param_vars:
                        self.transformer.local_vars.add(node.target.id)
                self.generic_visit(node)

            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Load):
                    if node.id not in self.transformer.param_vars:
                        self.transformer.local_vars.add(node.id)
                self.generic_visit(node)

        collector = VarCollector(self)
        collector.visit(node)

    def build_checkpoint_expression(self, base_checkpoint):
        """Build checkpoint expression accounting for loop offsets"""
        if not self.loop_offset_stack:
            return ast.Constant(value=base_checkpoint)

        expr = ast.Constant(value=base_checkpoint)

        for index_var, multiplier in self.loop_offset_stack:
            if multiplier is not None and multiplier > 0:
                offset_term = ast.BinOp(
                    left=ast.Name(id=index_var, ctx=ast.Load()),
                    op=ast.Mult(),
                    right=ast.Constant(value=multiplier),
                )
                expr = ast.BinOp(left=expr, op=ast.Add(), right=offset_term)

        return expr

    def is_pause_for_event_raise(self, stmt):
        """Check if a statement is raising PauseForEventException"""
        if isinstance(stmt, ast.Raise) and stmt.exc:
            if isinstance(stmt.exc, ast.Call):
                if isinstance(stmt.exc.func, ast.Name):
                    return stmt.exc.func.id == "PauseForEventException"
                elif isinstance(stmt.exc.func, ast.Attribute):
                    return stmt.exc.func.attr == "PauseForEventException"
        return False

    def visit_statement(self, stmt):
        """Process a statement and wrap in checkpoint guards"""
        if isinstance(stmt, ast.For):
            return self.transform_for_loop(stmt)

        if isinstance(stmt, ast.While):
            return self.transform_while_loop(stmt)

        if isinstance(stmt, ast.If):
            return self.transform_if_statement(stmt)

        if self.is_pause_for_event_raise(stmt):
            return self.wrap_pause_for_event_raise(stmt)

        if self.contains_await(stmt):
            return self.wrap_await_statement(stmt)

        should_wrap = (
            isinstance(stmt, ast.Assign)
            or isinstance(stmt, ast.AnnAssign)
            or isinstance(stmt, ast.AugAssign)
            or (isinstance(stmt, ast.Expr) and not isinstance(stmt.value, ast.Constant))
        )

        if should_wrap:
            checkpoint_num = self.checkpoint_counter
            self.checkpoint_counter += 1

            checkpoint_expr = self.build_checkpoint_expression(checkpoint_num)
            condition = ast.Compare(
                left=ast.Name(id="checkpoint", ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[checkpoint_expr],
            )

            if_body = [
                ast.Assign(
                    targets=[ast.Name(id="checkpoint", ctx=ast.Store())],
                    value=self.build_checkpoint_expression(checkpoint_num),
                ),
                stmt,
            ]

            if_stmt = ast.If(test=condition, body=if_body, orelse=[])

            return [if_stmt]
        else:
            return [stmt]

    def count_checkpoints_in_statements(self, statements):
        """Count how many checkpoints would be generated for a list of statements"""
        saved_counter = self.checkpoint_counter
        saved_loop_stack = self.loop_offset_stack[:]

        self.loop_offset_stack = []
        temp_counter = 0
        self.checkpoint_counter = temp_counter

        for stmt in statements:
            if isinstance(stmt, ast.For):
                temp_counter = self.checkpoint_counter
                temp_counter += self.count_checkpoints_in_statements(stmt.body)
                self.checkpoint_counter = temp_counter
            elif isinstance(stmt, ast.While):
                temp_counter = self.checkpoint_counter
                temp_counter += self.count_checkpoints_in_statements(stmt.body)
                self.checkpoint_counter = temp_counter
            elif isinstance(stmt, ast.If):
                temp_counter = self.checkpoint_counter
                temp_counter += self.count_checkpoints_in_statements(stmt.body)
                if stmt.orelse:
                    temp_counter += self.count_checkpoints_in_statements(stmt.orelse)
                self.checkpoint_counter = temp_counter
            elif (
                isinstance(stmt, ast.Assign)
                or isinstance(stmt, ast.AnnAssign)
                or isinstance(stmt, ast.AugAssign)
                or (
                    isinstance(stmt, ast.Expr)
                    and not isinstance(stmt.value, ast.Constant)
                )
                or self.contains_await(stmt)
            ):
                self.checkpoint_counter += 1

        count = self.checkpoint_counter - temp_counter

        self.checkpoint_counter = saved_counter
        self.loop_offset_stack = saved_loop_stack

        return count

    def transform_for_loop(self, for_stmt):
        """Transform a for loop by wrapping its body statements with iteration-aware checkpoints"""
        self.loop_counter += 1
        index_var = f"__index{self.loop_counter}__"
        self.local_vars.add(index_var)

        checkpoints_in_loop = self.count_checkpoints_in_statements(for_stmt.body)

        self.loop_offset_stack.append((index_var, checkpoints_in_loop))

        new_body = []
        for stmt in for_stmt.body:
            new_body.extend(self.visit_statement(stmt))

        index_increment = ast.AugAssign(
            target=ast.Name(id=index_var, ctx=ast.Store()),
            op=ast.Add(),
            value=ast.Constant(value=1),
        )
        new_body.append(index_increment)

        for_stmt.body = new_body

        if for_stmt.orelse:
            new_orelse = []
            for stmt in for_stmt.orelse:
                new_orelse.extend(self.visit_statement(stmt))
            for_stmt.orelse = new_orelse

        self.loop_offset_stack.pop()

        index_init = ast.Assign(
            targets=[ast.Name(id=index_var, ctx=ast.Store())],
            value=ast.Constant(value=0),
        )

        return [index_init, for_stmt]

    def transform_while_loop(self, while_stmt):
        """Transform a while loop by wrapping its body statements"""
        new_body = []
        for stmt in while_stmt.body:
            new_body.extend(self.visit_statement(stmt))

        while_stmt.body = new_body

        if while_stmt.orelse:
            new_orelse = []
            for stmt in while_stmt.orelse:
                new_orelse.extend(self.visit_statement(stmt))
            while_stmt.orelse = new_orelse

        return [while_stmt]

    def contains_raise(self, statements):
        """Check if any statement in the list is a raise statement"""
        for stmt in statements:
            if isinstance(stmt, ast.Raise):
                return True
        return False

    def transform_if_statement(self, if_stmt):
        """Transform an if statement by wrapping its body and orelse statements"""

        has_raise_in_body = self.contains_raise(if_stmt.body)

        if has_raise_in_body:
            checkpoint_num = self.checkpoint_counter
            self.checkpoint_counter += 1

            checkpoint_expr = self.build_checkpoint_expression(checkpoint_num)
            condition = ast.Compare(
                left=ast.Name(id="checkpoint", ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[checkpoint_expr],
            )

            condition_eval_body = [
                ast.Assign(
                    targets=[ast.Name(id="checkpoint", ctx=ast.Store())],
                    value=checkpoint_expr,
                ),
                ast.Assign(
                    targets=[ast.Name(id="___if_result___", ctx=ast.Store())],
                    value=if_stmt.test,
                ),
            ]

            condition_eval_if = ast.If(
                test=condition, body=condition_eval_body, orelse=[]
            )

            checkpoint_num = self.checkpoint_counter
            self.checkpoint_counter += 1

            checkpoint_expr = self.build_checkpoint_expression(checkpoint_num)
            condition = ast.Compare(
                left=ast.Name(id="checkpoint", ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[checkpoint_expr],
            )

            new_body = []
            for stmt in if_stmt.body:
                new_body.extend(self.visit_statement(stmt))

            new_orelse = []
            if if_stmt.orelse:
                for stmt in if_stmt.orelse:
                    if isinstance(stmt, ast.If):
                        new_orelse.extend(self.transform_if_statement(stmt))
                    else:
                        new_orelse.extend(self.visit_statement(stmt))

            modified_if = ast.If(
                test=ast.Name(id="___if_result___", ctx=ast.Load()),
                body=new_body,
                orelse=new_orelse,
            )

            if_execution_body = [
                ast.Assign(
                    targets=[ast.Name(id="checkpoint", ctx=ast.Store())],
                    value=checkpoint_expr,
                ),
                modified_if,
            ]

            if_execution_if = ast.If(test=condition, body=if_execution_body, orelse=[])

            return [condition_eval_if, if_execution_if]

        else:
            new_body = []
            for stmt in if_stmt.body:
                new_body.extend(self.visit_statement(stmt))
            if_stmt.body = new_body

            if if_stmt.orelse:
                new_orelse = []
                for stmt in if_stmt.orelse:
                    if isinstance(stmt, ast.If):
                        new_orelse.extend(self.transform_if_statement(stmt))
                    else:
                        new_orelse.extend(self.visit_statement(stmt))
                if_stmt.orelse = new_orelse

            return [if_stmt]

    def extract_return_variables(self, targets):
        """Extract return variable names/setters from assignment targets"""
        return_vars = []
        for target in targets:
            if isinstance(target, ast.Name):
                return_vars.append(target.id)
            elif isinstance(target, (ast.Subscript, ast.Attribute)):
                return_vars.append(ast.unparse(target))
            elif isinstance(target, ast.Tuple):
                for elt in target.elts:
                    if isinstance(elt, ast.Name):
                        return_vars.append(elt.id)
                    else:
                        return_vars.append(ast.unparse(elt))
        return return_vars

    def wrap_await_statement(self, stmt):
        """Wrap await statements in FunctionCall and CheckpointException"""
        checkpoint_num = self.checkpoint_counter
        self.checkpoint_counter += 1

        await_expr = None
        return_variables = []

        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await):
            await_expr = stmt.value.value
        elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Await):
            await_expr = stmt.value.value
            return_variables = self.extract_return_variables(stmt.targets)

        if await_expr and isinstance(await_expr, ast.Call):
            if isinstance(await_expr.func, ast.Name):
                fn_name = await_expr.func.id
            elif isinstance(await_expr.func, ast.Attribute):
                return self.wrap_regular_await(stmt, checkpoint_num)
            else:
                return self.wrap_regular_await(stmt, checkpoint_num)

            args_list = ast.List(elts=await_expr.args, ctx=ast.Load())

            kwargs_keys = [ast.Constant(value=kw.arg) for kw in await_expr.keywords]
            kwargs_values = [kw.value for kw in await_expr.keywords]
            kwargs_dict = ast.Dict(keys=kwargs_keys, values=kwargs_values)

            convert_call = ast.Call(
                func=ast.Name(id="_convert_args_to_kwargs", ctx=ast.Load()),
                args=[ast.Name(id=fn_name, ctx=ast.Load()), args_list, kwargs_dict],
                keywords=[],
            )

            return_vars_list = ast.List(
                elts=[ast.Constant(value=var) for var in return_variables],
                ctx=ast.Load(),
            )

            checkpoint_expr = self.build_checkpoint_expression(checkpoint_num)
            condition = ast.Compare(
                left=ast.Name(id="checkpoint", ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[checkpoint_expr],
            )

            first_if_body = [
                ast.Assign(
                    targets=[ast.Name(id="checkpoint", ctx=ast.Store())],
                    value=self.build_checkpoint_expression(checkpoint_num),
                ),
                ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(id="call_stack", ctx=ast.Load()),
                            attr="push",
                            ctx=ast.Load(),
                        ),
                        args=[
                            ast.Call(
                                func=ast.Name(id="FunctionCall", ctx=ast.Load()),
                                args=[ast.Name(id=fn_name, ctx=ast.Load())],
                                keywords=[
                                    ast.keyword(arg="kwargs", value=convert_call),
                                    ast.keyword(
                                        arg="return_variables", value=return_vars_list
                                    ),
                                ],
                            )
                        ],
                        keywords=[],
                    )
                ),
            ]

            first_if_stmt = ast.If(test=condition, body=first_if_body, orelse=[])

            checkpoint_num = self.checkpoint_counter
            self.checkpoint_counter += 1

            second_checkpoint_expr = self.build_checkpoint_expression(checkpoint_num)
            second_condition = ast.Compare(
                left=ast.Name(id="checkpoint", ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[second_checkpoint_expr],
            )

            second_if_body = [
                ast.Assign(
                    targets=[ast.Name(id="checkpoint", ctx=ast.Store())],
                    value=self.build_checkpoint_expression(checkpoint_num),
                ),
                ast.Raise(
                    exc=ast.Call(
                        func=ast.Name(id="CheckpointException", ctx=ast.Load()),
                        args=[
                            ast.Name(id="checkpoint", ctx=ast.Load()),
                            ast.Call(
                                func=ast.Name(id="locals", ctx=ast.Load()),
                                args=[],
                                keywords=[],
                            ),
                        ],
                        keywords=[],
                    ),
                    cause=None,
                ),
            ]

            second_if_stmt = ast.If(
                test=second_condition, body=second_if_body, orelse=[]
            )

            return [first_if_stmt, second_if_stmt]
        else:
            return self.wrap_regular_await(stmt, checkpoint_num)

    def wrap_pause_for_event_raise(self, stmt):
        """Wrap raise PauseForEventException in checkpoint guard so it can be skipped when event is received"""
        checkpoint_num = self.checkpoint_counter
        self.checkpoint_counter += 1

        checkpoint_expr = self.build_checkpoint_expression(checkpoint_num)
        condition = ast.Compare(
            left=ast.Name(id="checkpoint", ctx=ast.Load()),
            ops=[ast.Lt()],
            comparators=[checkpoint_expr],
        )

        if_body = [
            ast.Assign(
                targets=[ast.Name(id="checkpoint", ctx=ast.Store())],
                value=self.build_checkpoint_expression(checkpoint_num),
            ),
            stmt,
        ]

        if_stmt = ast.If(test=condition, body=if_body, orelse=[])

        return [if_stmt]

    def wrap_regular_await(self, stmt, checkpoint_num):
        """Fallback for complex await statements"""
        checkpoint_expr = self.build_checkpoint_expression(checkpoint_num)
        condition = ast.Compare(
            left=ast.Name(id="checkpoint", ctx=ast.Load()),
            ops=[ast.Lt()],
            comparators=[checkpoint_expr],
        )

        if_body = [
            ast.Assign(
                targets=[ast.Name(id="checkpoint", ctx=ast.Store())],
                value=self.build_checkpoint_expression(checkpoint_num),
            ),
            stmt,
        ]

        if_stmt = ast.If(test=condition, body=if_body, orelse=[])

        return [if_stmt]

    def contains_await(self, node):
        """Check if a node contains any await expressions"""

        class AwaitChecker(ast.NodeVisitor):
            def __init__(self):
                self.has_await = False

            def visit_Await(self, node):
                self.has_await = True

        checker = AwaitChecker()
        checker.visit(node)
        return checker.has_await


def make_durable(fn: Callable):
    """
    Public API: wrap a user async function into a DurableProgram.

    This callable can be awaited directly to execute the program using the
    default DurableBackend or can be used to create custom DurableRuntime
    instances.
    """
    from .runtime import DurableProgram

    return DurableProgram(fn)
