"""Gate for resume-editing and auto-apply entry points.

Discover does not edit a resume and does not apply to jobs. This flag only
reports whether those entry points would open. They stay shut until the
preference summary is stable.

Set DISCOVER_ACTIONS_ENABLED=1 to allow the gate once preferences are stable.
Leave it unset, or set it to 0, to keep the gate shut.
Set DISCOVER_ACTIONS_ENABLED=force to override the stable requirement.
The override still does not edit a resume or submit an application.
"""

from __future__ import annotations

import os

FLAG_ENV = "DISCOVER_ACTIONS_ENABLED"


def flag_mode() -> str:
    raw = os.environ.get(FLAG_ENV, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return "on"
    if raw in {"force", "override"}:
        return "force"
    return "off"


def gate(stable: bool) -> dict:
    mode = flag_mode()
    if mode == "force":
        allowed = True
        reason = ""
    elif mode == "on" and stable:
        allowed = True
        reason = ""
    elif not stable:
        allowed = False
        reason = "not_stable"
    else:
        allowed = False
        reason = "flag_off"
    return {
        "stable": stable,
        "flag": FLAG_ENV,
        "flag_mode": mode,
        "resume_edit": allowed,
        "auto_apply": allowed,
        "entry_allowed": allowed,
        "blocked_until_stable": not stable and mode != "force",
        "performed": False,
        "reason": reason,
    }
