"""Back-compat shim — prefer backend.agent.task_grounding for new code."""

from __future__ import annotations

from backend.agent.task_grounding import (  # noqa: F401
    annotate_audit_report,
    annotate_grounded_report,
    classify_all,
    classify_task,
    extra_iterations_for,
    extract_cited_paths,
    is_audit_like_task,
    is_grounded_task,
    maybe_annotate_audit_report,
    maybe_annotate_report,
    project_roots,
)

__all__ = [
    "annotate_audit_report",
    "annotate_grounded_report",
    "classify_all",
    "classify_task",
    "extract_cited_paths",
    "extra_iterations_for",
    "is_audit_like_task",
    "is_grounded_task",
    "maybe_annotate_audit_report",
    "maybe_annotate_report",
    "project_roots",
]
