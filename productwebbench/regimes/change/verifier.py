from __future__ import annotations

from pathlib import Path
from typing import Any

from ...evaluation.verification import verifier
from ...taxonomy.capability import CHANGE_REGIME


def verify_change_reference(
    *,
    tasks_path: Path,
    specs_path: Path,
    states_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    report = verifier.verify_specs(
        tasks_path=tasks_path,
        specs_path=specs_path,
        states_root=states_root,
        output_path=output_path,
    )
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "change_regime_reference_verification",
        "formal_task_record": False,
        "regime": CHANGE_REGIME,
        "wrapped_module": "productwebbench.evaluation.verification.verifier",
        "report": report,
    }


def verify_change_submission(
    *,
    tasks_path: Path,
    specs_path: Path,
    states_root: Path,
    output_path: Path,
    design_anchors_path: Path | None = None,
) -> dict[str, Any]:
    report = verifier.verify_submission_specs(
        tasks_path=tasks_path,
        specs_path=specs_path,
        states_root=states_root,
        output_path=output_path,
        design_anchors_path=design_anchors_path,
    )
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "change_regime_submission_verification",
        "formal_task_record": False,
        "regime": CHANGE_REGIME,
        "wrapped_module": "productwebbench.evaluation.verification.verifier",
        "report": report,
    }
