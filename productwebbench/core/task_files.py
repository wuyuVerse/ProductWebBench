from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


FILE_LIST_FIELDS = ("suggested_files", "assets_to_consider")


def normalize_task_path(value: object) -> str:
    normalized = str(value).strip().replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized


def unique_path_values(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = normalize_task_path(value)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(str(value))
    return unique


def task_file_patterns(task: dict[str, Any], fields: tuple[str, ...] = FILE_LIST_FIELDS) -> list[str]:
    values: list[object] = []
    for field in fields:
        field_values = task.get(field, [])
        if isinstance(field_values, list):
            values.extend(field_values)
    return unique_path_values(values)


def cross_field_path_duplicates(task: dict[str, Any], fields: tuple[str, ...] = FILE_LIST_FIELDS) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = defaultdict(list)
    for field in fields:
        values = task.get(field, [])
        if not isinstance(values, list):
            continue
        field_seen: set[str] = set()
        for value in values:
            key = normalize_task_path(value)
            if key and key not in field_seen:
                owners[key].append(field)
                field_seen.add(key)
    return {path: field_names for path, field_names in sorted(owners.items()) if len(field_names) > 1}
