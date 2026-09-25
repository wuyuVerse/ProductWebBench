from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Iterable, Iterator, TypeVar


T = TypeVar("T")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def to_jsonable(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    return value


def write_jsonl(path: Path, records: Iterable[object]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(to_jsonable(record), ensure_ascii=False, sort_keys=True))
            f.write("\n")


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_json(path: Path, data: object) -> None:
    ensure_dir(path.parent)
    path.write_text(
        json.dumps(to_jsonable(data), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

