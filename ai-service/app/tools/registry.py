from typing import Any, Callable

from pydantic import BaseModel


class ToolSpec:
    def __init__(self, name: str, input_schema: type[BaseModel], fn: Callable[..., dict]):
        self.name = name
        self.input_schema = input_schema
        self.fn = fn


TOOL_REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, input_schema: type[BaseModel]):
    def decorator(fn: Callable[..., dict]) -> Callable[..., dict]:
        TOOL_REGISTRY[name] = ToolSpec(name, input_schema, fn)
        return fn
    return decorator
