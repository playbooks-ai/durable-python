"""Utility functions for the durable-python package."""

import inspect
from typing import Callable


def _convert_args_to_kwargs(fn: Callable, args: list, kwargs: dict) -> dict:
    """
    Convert positional arguments to keyword arguments by inspecting the function signature.

    Args:
        fn: The function to call
        args: List of positional arguments
        kwargs: Dictionary of keyword arguments

    Returns:
        A dictionary with all arguments as keyword arguments
    """
    try:
        sig = inspect.signature(fn)
        param_names = list(sig.parameters.keys())

        # Convert positional args to kwargs
        all_kwargs = {}
        for i, arg in enumerate(args):
            if i < len(param_names):
                all_kwargs[param_names[i]] = arg

        # Merge with existing kwargs (kwargs take precedence)
        all_kwargs.update(kwargs)

        return all_kwargs
    except (ValueError, TypeError):
        # If we can't inspect the function, return kwargs as-is with positional args ignored
        return kwargs
