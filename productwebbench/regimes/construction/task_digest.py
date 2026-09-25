from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


TOP_LEVEL_EVIDENCE_KEYS = {
    "reference_actor_trace",
    "reference_trajectory_report",
}
# The stable task digest binds evidence (traces, trajectory reports, metareval)
# to the immutable TASK CONTRACT (repo/task ids, lm/mm task, milestone ladder,
# split, asset policy spec, website type, ...). It must NOT depend on audit
# OUTCOMES. The entire ``gates`` map holds such outcomes — each gate is a
# "did this audit pass" verdict that the pipeline populates and re-populates as
# evidence is attached (spec_completeness_precheck at attach-trace,
# trajectory_regression_sanity/regression_sanity at finalize, metareval later).
# Including any of them made the digest drift between evidence steps, so the
# just-baked evidence's ``task_sha256`` no longer matched the task and
# finalize/precheck/accept could never agree. Excluding the whole gate map keeps
# the identity stable across the pipeline and is robust to future gate keys.


def task_contract_payload(task: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(task)
    for key in TOP_LEVEL_EVIDENCE_KEYS:
        payload.pop(key, None)
    # Gates are audit outcomes, not task-contract identity: drop them entirely.
    payload.pop("gates", None)
    return payload


def stable_task_digest(task: dict[str, Any]) -> str:
    text = json.dumps(task_contract_payload(task), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
