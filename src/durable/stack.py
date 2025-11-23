"""Core execution primitives: CodeBlock, FunctionCall, CallStack."""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import textwrap
from collections import deque
from typing import Any, Callable, Iterable

import cloudpickle

from .exceptions import CheckpointException, PauseForEventException
from .transform import transform_to_durable_ast
from .utils import _convert_args_to_kwargs


def _serialize_value(value: Any) -> bytes:
    """Best-effort serialization that drops unpicklable callables."""
    try:
        return cloudpickle.dumps(value)
    except Exception:
        if isinstance(value, dict):
            cleaned: dict[Any, Any] = {}
            for key, item in value.items():
                if callable(item):
                    continue
                try:
                    cloudpickle.dumps(item)
                    cleaned[key] = item
                except Exception:
                    cleaned[key] = repr(item)
            return cloudpickle.dumps(cleaned)
        return cloudpickle.dumps(repr(value))


class CodeBlock:
    """Chunk of durable code wrapped in a function."""

    def __init__(
        self,
        fn: Callable[..., Any],
        locals: dict | None = None,
        return_variables: list[str] | None = None,
    ):
        self.fn = fn
        self.fname = fn.__name__
        self.return_variables = return_variables or []
        self.return_values: list[Any] = []

        closure_vars: dict[str, Any] = {}
        try:
            closure = inspect.getclosurevars(fn)
            if closure.nonlocals:
                closure_vars.update(closure.nonlocals)
        except (TypeError, ValueError):
            pass

        self.locals = {
            **closure_vars,
            **(locals or {}),
        }
        self.locals.setdefault("checkpoint", -1)
        self.locals["current_call"] = self

        self.finished = False
        self.checkpoints: dict[int, dict[str, Any]] = {}
        self.transformed_fn: Callable[..., Any] | None = None
        self.transformed_code: str | None = None
        self.param_names: list[str] = []

        self.make_durable()

    def make_durable(self) -> None:
        """Transform the wrapped function into its durable form."""
        if getattr(self.fn, "__durable_transformed__", False):
            return

        try:
            source = inspect.getsource(self.fn)
            source = textwrap.dedent(source)
            parsed = ast.parse(source)
            func_def = parsed.body[0]
            if isinstance(func_def, ast.AsyncFunctionDef):
                args = func_def.args
                param_names: list[str] = []
                param_names.extend([arg.arg for arg in args.posonlyargs])
                param_names.extend([arg.arg for arg in args.args])
                if args.vararg:
                    param_names.append(args.vararg.arg)
                param_names.extend([arg.arg for arg in args.kwonlyargs])
                if args.kwarg:
                    param_names.append(args.kwarg.arg)
                self.param_names = param_names
        except (OSError, TypeError, IndexError):
            self.param_names = []

        try:
            transformed_ast = transform_to_durable_ast(self.fn)
        except Exception:
            return

        try:
            self.transformed_code = ast.unparse(transformed_ast)
        except Exception:
            self.transformed_code = None
            return

        namespace: dict[str, Any] = {}
        exec(self.transformed_code, namespace)
        self.transformed_fn = namespace.get(self.fname)
        if self.transformed_fn:
            self.transformed_fn.__durable_transformed__ = True

    def save_checkpoint(self, checkpoint: int, namespace: dict) -> None:
        """Persist locals for a checkpoint."""
        self.checkpoints[checkpoint] = namespace

    def load_checkpoint(self, checkpoint: int, namespace: dict) -> dict:
        """Restore locals from a checkpoint."""
        if checkpoint > -1 and checkpoint in self.checkpoints:
            for key, value in self.checkpoints[checkpoint].items():
                namespace[key] = value
        return namespace

    async def run_dynamic_async_code(
        self,
        fname: str,
        code_string: str,
        custom_globals: dict,
        custom_locals: dict,
    ) -> dict:
        """Execute transformed async code string."""
        execution_context = {
            "asyncio": asyncio,
            "_convert_args_to_kwargs": _convert_args_to_kwargs,
            "FunctionCall": FunctionCall,
            "CheckpointException": CheckpointException,
            **custom_globals,
            **custom_locals,
        }

        exec(code_string, execution_context)
        coro_func = execution_context.get(fname)

        event_pending: PauseForEventException | None = None
        exception_pending: Exception | None = None

        if coro_func:
            try:
                self.return_values = []
                retvals = await coro_func()
                if retvals:
                    if not isinstance(retvals, tuple):
                        retvals = (retvals,)
                    self.return_values = list(retvals)
            except PauseForEventException as exc:
                event_pending = exc
            except CheckpointException as exc:
                exception_pending = exc
            except Exception as exc:
                exception_pending = exc

        special_vars = {
            "__builtins__",
            "__name__",
            "__doc__",
            "__package__",
            "__loader__",
            "__spec__",
        }
        import types

        for key, value in execution_context.items():
            if key in custom_globals or key in special_vars:
                continue
            if isinstance(value, types.ModuleType):
                continue
            custom_locals[key] = value

        if event_pending:
            raise event_pending
        if exception_pending:
            raise exception_pending

        return custom_locals

    async def run(
        self,
        call: "FunctionCall",
        namespace: dict,
        pending_return_values: dict | None = None,
    ) -> bool:
        """Execute this block until completion or pause."""
        self.locals = self.load_checkpoint(
            self.locals.get("checkpoint", -1), self.locals
        )

        if pending_return_values:
            for var_name, value in pending_return_values.items():
                if var_name.isidentifier():
                    self.locals[var_name] = value
                else:
                    try:
                        exec(
                            f"{var_name} = __value__", self.locals, {"__value__": value}
                        )
                    except Exception:
                        self.locals[var_name] = value

        if self.transformed_code is None:
            sig = inspect.signature(self.fn)
            kwargs: dict[str, Any] = {}
            for param_name in sig.parameters:
                if param_name in self.locals:
                    kwargs[param_name] = self.locals[param_name]
            result = await self.fn(**kwargs)
            if result is not None:
                if isinstance(result, tuple):
                    self.return_values = list(result)
                else:
                    self.return_values = [result]
            self.finished = True
            return True

        try:
            self.locals = await self.run_dynamic_async_code(
                fname=self.fname,
                code_string=self.transformed_code,
                custom_globals=namespace,
                custom_locals=self.locals,
            )
        except CheckpointException as exc:
            self.save_checkpoint(exc.checkpoint, exc.namespace)
            self.locals["checkpoint"] = exc.checkpoint
            return False
        except PauseForEventException:
            self.save_checkpoint(self.locals.get("checkpoint", -1), self.locals)
            raise

        self.finished = True
        return True

    def to_state(self) -> dict:
        """Serialize block into a dictionary for persistence."""
        return {
            "fn_ref": {
                "module": self.fn.__module__,
                "qualname": self.fn.__qualname__,
            },
            "locals": _serialize_value(self.locals),
            "return_variables": self.return_variables,
            "return_values": _serialize_value(self.return_values),
            "finished": self.finished,
            "checkpoints": {
                k: _serialize_value(v) for k, v in self.checkpoints.items()
            },
            "transformed_code": self.transformed_code,
        }

    @classmethod
    def from_state(cls, state: dict) -> "CodeBlock":
        fn_ref = state.get("fn_ref")
        fn = None
        if fn_ref:
            module = importlib.import_module(fn_ref["module"])
            fn = module
            for part in fn_ref["qualname"].split("."):
                fn = getattr(fn, part)
        else:
            fn = cloudpickle.loads(state["fn"])
        locals = cloudpickle.loads(state["locals"])
        block = cls(
            fn=fn, locals=locals, return_variables=state.get("return_variables") or []
        )
        block.return_values = cloudpickle.loads(state["return_values"])
        block.finished = state.get("finished", False)
        block.checkpoints = {
            k: cloudpickle.loads(v) for k, v in (state.get("checkpoints") or {}).items()
        }
        block.transformed_code = state.get("transformed_code")
        return block

    def __str__(self) -> str:
        locals = {k: v for k, v in self.locals.items() if k != "current_call"}
        return f"    {self.fname}(locals={locals}, return_variables={self.return_variables}, finished={self.finished})"


class FunctionCall:
    """Represents one durable function invocation."""

    def __init__(
        self,
        fn: Callable[..., Any] | None,
        kwargs: dict | None = None,
        locals: dict | None = None,
        return_variables: list[str] | None = None,
        code_blocks: Iterable[CodeBlock] | None = None,
    ):
        merged_locals = {**(kwargs or {}), **(locals or {})}

        self.locals: dict[str, Any] = merged_locals
        self.return_variables = return_variables or []
        if code_blocks is None:
            if fn is None:
                raise ValueError("fn is required when code_blocks is not provided")
            self.code_blocks: list[CodeBlock] = [
                CodeBlock(
                    fn, locals=self.locals, return_variables=self.return_variables
                )
            ]
        else:
            self.code_blocks = list(code_blocks)
        self.current_index = 0
        self.finished = False
        self.exhausted = False
        self.return_values: list[Any] = []

    def append_code_block(self, block: CodeBlock) -> None:
        """Append a new code block (used for streaming code)."""
        self.code_blocks.append(block)
        self.exhausted = False

    async def resume(
        self,
        call_stack: "CallStack",
        namespace: dict,
        pending_return_values: dict | None = None,
    ) -> None:
        """Resume execution of this call starting at the current block."""
        self.exhausted = False
        while self.code_blocks:
            block = self.code_blocks[0]
            block.locals = {**self.locals, **block.locals}
            self.locals = block.locals

            await block.run(
                call=self,
                namespace=namespace,
                pending_return_values=pending_return_values,
            )
            pending_return_values = None

            if block.finished:
                self.return_values = block.return_values
                self.locals = block.locals
                self.locals["checkpoint"] = -1
                self.code_blocks.pop(0)
                if self.code_blocks:
                    self.code_blocks[0].locals = {
                        **self.locals,
                        **self.code_blocks[0].locals,
                    }
                    continue
                self.finished = True
                self.exhausted = True
            else:
                self.exhausted = True
                break
        if not self.code_blocks and not self.finished:
            self.exhausted = True

    def to_state(self) -> dict:
        """Serialize this call."""
        return {
            "locals": _serialize_value(self.locals),
            "return_variables": self.return_variables,
            "return_values": _serialize_value(self.return_values),
            "current_index": self.current_index,
            "finished": self.finished,
            "exhausted": self.exhausted,
            "code_blocks": [block.to_state() for block in self.code_blocks],
        }

    @classmethod
    def from_state(cls, state: dict) -> "FunctionCall":
        locals = cloudpickle.loads(state["locals"])
        code_blocks = [CodeBlock.from_state(b) for b in state.get("code_blocks") or []]
        call = cls(
            fn=None if code_blocks else lambda: None,
            locals=locals,
            return_variables=state.get("return_variables") or [],
            code_blocks=code_blocks,
        )
        call.return_values = cloudpickle.loads(state["return_values"])
        call.current_index = state.get("current_index", 0)
        call.finished = state.get("finished", False)
        call.exhausted = state.get("exhausted", False)
        return call

    def __str__(self) -> str:
        locals = {k: v for k, v in self.locals.items() if k != "current_call"}
        return f"  Call(locals={locals}, return_variables={self.return_variables}, finished={self.finished})"


class CallStack:
    """Stack of nested FunctionCall objects."""

    def __init__(self, calls: Iterable[FunctionCall] | None = None):
        self.stack: deque[FunctionCall] = deque(calls or [])
        self.pending_return_values: dict | None = None
        self.last_result: Any = None

    def is_empty(self) -> bool:
        return len(self.stack) == 0

    def peek(self) -> FunctionCall | None:
        if self.is_empty():
            return None
        return self.stack[-1]

    def push(self, call: FunctionCall) -> None:
        self.stack.append(call)

    def pop(self) -> FunctionCall:
        return self.stack.pop()

    async def _resume(self, namespace: dict) -> None:
        call = self.peek()
        if call is None:
            return

        await call.resume(
            call_stack=self,
            namespace=namespace,
            pending_return_values=self.pending_return_values,
        )
        self.pending_return_values = None

        if call.finished:
            finished_call = self.pop()
            if finished_call.return_values and finished_call.return_variables:
                self.pending_return_values = {}
                for i, var_name in enumerate(finished_call.return_variables):
                    if i < len(finished_call.return_values):
                        self.pending_return_values[var_name] = (
                            finished_call.return_values[i]
                        )
            elif finished_call.return_values:
                self.last_result = (
                    finished_call.return_values[0]
                    if len(finished_call.return_values) == 1
                    else tuple(finished_call.return_values)
                )
        elif call.exhausted:
            return

    async def resume(self, namespace: dict) -> None:
        while not self.is_empty():
            await self._resume(namespace=namespace)
            top = self.peek()
            if top and top.exhausted and not top.finished:
                break

    def to_state(self) -> dict:
        return {
            "calls": [call.to_state() for call in self.stack],
            "pending_return_values": _serialize_value(self.pending_return_values),
            "last_result": _serialize_value(self.last_result),
        }

    @classmethod
    def from_state(cls, state: dict) -> "CallStack":
        calls = [FunctionCall.from_state(c) for c in state.get("calls") or []]
        stack = cls(calls=calls)
        pending_blob = state.get("pending_return_values")
        stack.pending_return_values = (
            cloudpickle.loads(pending_blob) if pending_blob is not None else None
        )
        last_result_blob = state.get("last_result")
        stack.last_result = (
            cloudpickle.loads(last_result_blob)
            if last_result_blob is not None
            else None
        )
        return stack

    def __str__(self) -> str:
        strs = ["-" * 10, f"Stack(pending_return_values={self.pending_return_values})"]
        for call in self.stack:
            strs.append(call.__str__())
        strs.append("-" * 10)
        return "\n".join(strs)
