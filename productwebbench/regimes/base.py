from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RegimeContext:
    task_id: str
    repo_id: str
    workspace_root: Path
    states_root: Path
    output_root: Path


class ContinuityRegime(ABC):
    key: str
    label: str = ""
    primary_metrics: tuple[str, ...] = ()

    @abstractmethod
    def author_task(self, context: RegimeContext, target: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a non-formal per-task authoring plan for this regime.

        This method must not generate accepted benchmark data. It defines the
        required evidence and gates that a human/agent-authored task must pass.
        """

    @abstractmethod
    def build_verifier(self, context: RegimeContext, task: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a non-formal verifier construction plan for this regime.

        This method must not write verifier specs for accepted tasks. It defines
        required verifier layers and sanity checks for one inspected task.
        """

    @abstractmethod
    def score(self, report: dict[str, Any]) -> dict[str, Any]:
        """Return regime-specific score output for an evaluation report."""

    @abstractmethod
    def package(self, context: RegimeContext) -> dict[str, Any]:
        """Return package metadata for this regime."""
