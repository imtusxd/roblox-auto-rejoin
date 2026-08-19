"""Process-level resource management: priority class + CPU affinity,
matching YummyWebPlayer's "Set Priority Low" / "Set Affinity" / "Affinity
Core" settings. This machine is already confirmed resource-constrained
running backend + eldorado-bot + Roblox + Chrome together, so keeping
launched Roblox clients from fighting the delivery bot for CPU time matters
more here than it would on a dedicated farming rig.

Thin wrapper around psutil so the actual policy (`apply_resource_policy`)
stays easy to unit test against a fake process object.
"""
from __future__ import annotations

import dataclasses

import psutil

PRIORITY_MAP = {
    "normal": psutil.NORMAL_PRIORITY_CLASS,
    "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "low": psutil.IDLE_PRIORITY_CLASS,
}


@dataclasses.dataclass(frozen=True)
class ResourcePolicy:
    priority: str = "normal"  # "normal" | "below_normal" | "low"
    cpu_affinity_core_count: int = 0  # 0 = leave affinity untouched


def apply_resource_policy(pid: int, policy: ResourcePolicy) -> bool:
    """Best-effort - a process that already exited or that we don't have
    permission to touch just means the policy silently doesn't apply,
    rather than crashing the watch loop over what's a resource-usage nicety
    and not a correctness requirement."""
    try:
        process = psutil.Process(pid)

        priority_class = PRIORITY_MAP.get(policy.priority)
        if priority_class is not None and policy.priority != "normal":
            process.nice(priority_class)

        if policy.cpu_affinity_core_count > 0:
            available = list(range(psutil.cpu_count() or 1))
            cores = available[: policy.cpu_affinity_core_count] or available
            process.cpu_affinity(cores)

        return True
    except Exception:
        return False
