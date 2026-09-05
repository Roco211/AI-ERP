"""Closed JSON codec: never deserialize a module name, pickle or arbitrary class."""

import json
from typing import Any

from langgraph.types import Interrupt

MAX_CHECKPOINT_BYTES = 1_048_576
MAX_METADATA_BYTES = 16_384
MAX_CHECKPOINT_DEPTH = 64
MAX_CHECKPOINT_NODES = 50_000


class CheckpointJSONCodec:
    """Only plain JSON, tuple and the framework's explicit Interrupt value.

    Every container has an envelope, so a business dict resembling a codec tag
    remains a dict. The only instantiated non-JSON type is the pinned Interrupt
    class; it is imported by this source file, never selected by stored content.
    Business Decimal and UUID values must already be DTO JSON strings. Messages,
    RuntimeContext and model SDK instances are deliberately unsupported.
    """

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        budget = [MAX_CHECKPOINT_NODES]
        value = {"version": 1, "value": self._encode(obj, 0, budget)}
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > MAX_CHECKPOINT_BYTES:
            raise ValueError("Checkpoint exceeds the JSON size limit")
        return "forge-json-v1", data

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        if data[0] != "forge-json-v1" or len(data[1]) > MAX_CHECKPOINT_BYTES:
            raise ValueError("Unsupported checkpoint encoding or size")
        try:
            value = json.loads(data[1])
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValueError("Invalid checkpoint JSON") from exc
        if (
            type(value) is not dict
            or set(value) != {"version", "value"}
            or type(value["version"]) is not int
            or value["version"] != 1
        ):
            raise ValueError("Unsupported checkpoint JSON version")
        return self._decode(value["value"], 0, [MAX_CHECKPOINT_NODES])

    @staticmethod
    def _consume(depth: int, budget: list[int]) -> None:
        budget[0] -= 1
        if depth > MAX_CHECKPOINT_DEPTH or budget[0] < 0:
            raise ValueError("Checkpoint exceeds the JSON structure limit")

    def _encode(self, value: Any, depth: int, budget: list[int]) -> Any:
        self._consume(depth, budget)
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is str:
            if "\x00" in value:
                raise ValueError("Checkpoint JSON cannot contain a null character")
            return value
        if type(value) is dict:
            pairs = []
            for key, item in value.items():
                if type(key) is not str or "\x00" in key:
                    raise ValueError("Checkpoint JSON requires string keys")
                pairs.append([key, self._encode(item, depth + 1, budget)])
            return ["dict", pairs]
        if type(value) in (list, tuple):
            return [
                "tuple" if type(value) is tuple else "list",
                [self._encode(item, depth + 1, budget) for item in value],
            ]
        if type(value) is Interrupt:
            if type(value.id) is not str or len(value.id) > 128 or "\x00" in value.id:
                raise ValueError("Invalid checkpoint interrupt ID")
            return ["interrupt", value.id, self._encode(value.value, depth + 1, budget)]
        raise ValueError("Checkpoint contains an unsupported non-JSON value")

    def _decode(self, value: Any, depth: int, budget: list[int]) -> Any:
        self._consume(depth, budget)
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is str:
            if "\x00" in value:
                raise ValueError("Invalid checkpoint string")
            return value
        if type(value) is not list or not value or type(value[0]) is not str:
            raise ValueError("Unsupported checkpoint JSON value")
        tag = value[0]
        if tag in ("list", "tuple") and len(value) == 2 and type(value[1]) is list:
            items = [self._decode(item, depth + 1, budget) for item in value[1]]
            return tuple(items) if tag == "tuple" else items
        if tag == "dict" and len(value) == 2 and type(value[1]) is list:
            result = {}
            for pair in value[1]:
                if (
                    type(pair) is not list
                    or len(pair) != 2
                    or type(pair[0]) is not str
                    or "\x00" in pair[0]
                    or pair[0] in result
                ):
                    raise ValueError("Invalid checkpoint dictionary")
                result[pair[0]] = self._decode(pair[1], depth + 1, budget)
            return result
        if (
            tag == "interrupt"
            and len(value) == 3
            and type(value[1]) is str
            and len(value[1]) <= 128
            and "\x00" not in value[1]
        ):
            return Interrupt(value=self._decode(value[2], depth + 1, budget), id=value[1])
        raise ValueError("Unknown checkpoint JSON tag")
