# -*- coding: utf-8 -*-
"""Read-only safety helpers shared by the Python media scripts.

The skill's contract is: read and parse WeChat data, never write to it,
never forward it, never add/delete/edit chat content. Node-side enforcement
is the weflow-cli command allowlist; on the Python side the rule is that every
output directory must live outside the WeChat account directory.
"""
from __future__ import annotations

from pathlib import Path


def is_inside(parent, child) -> bool:
    """True when `child` is `parent` or below it (resolved paths)."""
    try:
        parent_path = Path(parent).resolve()
        child_path = Path(child).resolve()
    except OSError:
        return False
    if parent_path == child_path:
        return True
    try:
        child_path.relative_to(parent_path)
        return True
    except ValueError:
        return False


def assert_outside(root, path, label="输出目录") -> None:
    """Abort when `path` is inside the WeChat data directory `root`.

    This is a hard stop, not a warning: writing decoded media or a sandbox
    inside the source tree would violate the read-only guarantee.
    """
    if not root or not path:
        return
    if is_inside(root, path):
        raise SystemExit(
            f"拒绝写入微信数据目录（只读约束）：{label} {path} 位于 {root} 内"
        )
