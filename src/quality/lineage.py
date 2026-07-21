"""
src/quality/lineage.py — Cross-artifact-type derivation graph (docs/ADR/0011).

Reconstructs a directed acyclic graph of "derived from" edges
(``VersionRecord.lineage_parents``) on demand from every ``version.json``
found under the configured ``quality_engineering.lineage.artifact_roots``
directories — nothing is persisted as a second source of truth (ADR-0011
Decision 2).

This is deliberately distinct from same-type version history
(``VersionRecord.previous_artifact_id``, walked by
:mod:`src.quality.regression` and :mod:`src.quality.versioning` instead) —
see ADR-0011 Decision 1. ``trace_ancestors`` does re-express a node's own
``previous_artifact_id`` chain as lineage nodes for display purposes (the
documented consequence of ADR-0011 Decision 4), but ``trace_descendants``
never does the reverse — it only follows genuine cross-type
``lineage_parents`` edges, so it correctly returns empty for every dataset
today (nothing has trained on a dataset yet).

This module's core API (``build_lineage_graph``, ``trace_ancestors``,
``trace_descendants``, ``evaluate_lineage_check``) depends only on
``Artifact`` / ``VersionRecord`` (:mod:`src.quality.schemas`) and
``QualityEngineeringConfig`` (:mod:`src.quality.config`) — no scoring,
coverage, or review types — so it has no actual coupling to the rest of
:mod:`src.quality` beyond its package path (see ADR-0011's "Implementation
Note"). The one exception is :func:`register_lineage_section` at the
bottom of this file, which registers this module's own dashboard section
(docs/ADR/0009-engineering-dashboard.md Decision 6) — it imports
:mod:`src.quality.dashboard` inside the function body, not at module
level, both to avoid a real circular import (dashboard.py imports this
module too, to trigger the registration) and to keep the dashboard
dependency isolated to that one function.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from src.quality.artifacts import ArtifactNotFoundError, load_version_record
from src.quality.config import QualityEngineeringConfig
from src.quality.schemas import GateCheckResult, VersionRecord

if TYPE_CHECKING:
    from src.quality.dashboard import DashboardContext


@dataclasses.dataclass
class LineageNode:
    """One artifact version, as seen by the lineage graph.

    Args:
        artifact_type: The artifact's type, e.g. ``"dataset"``.
        artifact_id: The artifact's own identity.
        artifact_dir: Directory this node's ``version.json`` was read from.
        version: The full :class:`~src.quality.schemas.VersionRecord`.
    """

    artifact_type: str
    artifact_id: str
    artifact_dir: Path
    version: VersionRecord


@dataclasses.dataclass
class LineageGraph:
    """The full derivation graph reconstructed by :func:`build_lineage_graph`.

    Args:
        nodes: Every discovered artifact version, keyed
            ``"{artifact_type}:{artifact_id}"``.
        edges: Every ``lineage_parents`` edge, as
            ``(child_key, parent_key, relation)`` triples.
    """

    nodes: dict[str, LineageNode]
    edges: list[tuple[str, str, str]]


def _node_key(artifact_type: str, artifact_id: str) -> str:
    """Return the canonical graph key for one artifact version.

    Args:
        artifact_type: The artifact's type.
        artifact_id: The artifact's own identity.

    Returns:
        ``"{artifact_type}:{artifact_id}"``.
    """
    return f"{artifact_type}:{artifact_id}"


def build_lineage_graph(cfg: QualityEngineeringConfig) -> LineageGraph:
    """Scan every configured artifact root and assemble the lineage graph.

    Args:
        cfg: Resolved engineering-loop configuration (uses
            ``cfg.lineage.artifact_roots``).

    Returns:
        A :class:`LineageGraph` covering every artifact version found. A
        configured root that does not exist on disk yet (e.g.
        ``"evaluation"`` before Phase 5 ships) is silently skipped —
        never an error (ADR-0011 Decision 3). A subdirectory without a
        ``version.json`` (never versioned) is likewise skipped.
    """
    nodes: dict[str, LineageNode] = {}
    edges: list[tuple[str, str, str]] = []

    for artifact_type, root in cfg.lineage.artifact_roots.items():
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for artifact_dir in sorted(p for p in root_path.iterdir() if p.is_dir()):
            try:
                version = load_version_record(artifact_dir)
            except ArtifactNotFoundError:
                continue
            key = _node_key(artifact_type, version.artifact_id)
            nodes[key] = LineageNode(
                artifact_type=artifact_type,
                artifact_id=version.artifact_id,
                artifact_dir=artifact_dir,
                version=version,
            )
            for parent in version.lineage_parents:
                parent_key = _node_key(parent.parent_artifact_type, parent.parent_artifact_id)
                edges.append((key, parent_key, parent.relation))

    return LineageGraph(nodes=nodes, edges=edges)


def trace_ancestors(graph: LineageGraph, artifact_type: str, artifact_id: str) -> list[LineageNode]:
    """Return every node the given artifact was directly or transitively derived from.

    Walks two kinds of edges outward from the start node: genuine
    cross-type ``lineage_parents`` edges (ADR-0011 Decision 1), and each
    visited node's own ``previous_artifact_id`` (re-expressed as a
    lineage node for display consistency — ADR-0011 Decision 4's
    documented consequence). Both directions terminate at nodes with no
    corresponding ``version.json`` on disk.

    Args:
        graph: A :class:`LineageGraph`, typically from
            :func:`build_lineage_graph`.
        artifact_type: The starting artifact's type.
        artifact_id: The starting artifact's identity.

    Returns:
        Every ancestor node reachable from the start node, nearest first,
        with no duplicates. Empty if the start node itself is not in
        *graph* or has no ancestors.
    """
    start_key = _node_key(artifact_type, artifact_id)
    visited: set[str] = {start_key}
    result: list[LineageNode] = []
    _walk_ancestors(graph, start_key, visited, result)
    return result


def _walk_ancestors(
    graph: LineageGraph, key: str, visited: set[str], result: list[LineageNode],
) -> None:
    """Depth-first helper for :func:`trace_ancestors`. Mutates *visited* and *result*."""
    for child_key, parent_key, _relation in graph.edges:
        if child_key == key and parent_key not in visited:
            visited.add(parent_key)
            parent_node = graph.nodes.get(parent_key)
            if parent_node is not None:
                result.append(parent_node)
                _walk_ancestors(graph, parent_key, visited, result)

    node = graph.nodes.get(key)
    if node is not None and node.version.previous_artifact_id is not None:
        prev_key = _node_key(node.artifact_type, node.version.previous_artifact_id)
        if prev_key not in visited:
            visited.add(prev_key)
            prev_node = graph.nodes.get(prev_key)
            if prev_node is not None:
                result.append(prev_node)
                _walk_ancestors(graph, prev_key, visited, result)


def trace_descendants(
    graph: LineageGraph, artifact_type: str, artifact_id: str,
) -> list[LineageNode]:
    """Return every node directly or transitively derived from the given artifact.

    Only follows genuine cross-type ``lineage_parents`` edges — never the
    reverse of ``previous_artifact_id`` (ADR-0011 Decision 4's documented
    consequence: this correctly returns an empty list for every dataset
    today, since nothing has trained on a dataset yet).

    Args:
        graph: A :class:`LineageGraph`, typically from
            :func:`build_lineage_graph`.
        artifact_type: The starting artifact's type.
        artifact_id: The starting artifact's identity.

    Returns:
        Every descendant node reachable from the start node, nearest
        first, with no duplicates.
    """
    start_key = _node_key(artifact_type, artifact_id)
    visited: set[str] = {start_key}
    result: list[LineageNode] = []
    _walk_descendants(graph, start_key, visited, result)
    return result


def _walk_descendants(
    graph: LineageGraph, key: str, visited: set[str], result: list[LineageNode],
) -> None:
    """Depth-first helper for :func:`trace_descendants`. Mutates *visited* and *result*."""
    for child_key, parent_key, _relation in graph.edges:
        if parent_key == key and child_key not in visited:
            visited.add(child_key)
            child_node = graph.nodes.get(child_key)
            if child_node is not None:
                result.append(child_node)
                _walk_descendants(graph, child_key, visited, result)


def evaluate_lineage_check(
    version: VersionRecord,
    *,
    expected_parent_type: str,
    expected_parent_id: str,
    check_name: str = "lineage_parent",
) -> GateCheckResult:
    """Check whether *version* records a specific artifact as a lineage parent.

    Formalizes what docs/ADR/0010-future-ml-integration.md Section 2
    described informally as an ad hoc field comparison (ADR-0011
    Decision 5) — e.g. "the checkpoint about to be evaluated must have
    trained on this exact dataset version." Not registered into any
    active gate check list in this phase (no model artifacts exist yet
    to check) — implemented and unit-tested now so Phase 4 activates it
    by registering it, rather than writing it from scratch.

    Args:
        version: The candidate artifact's own :class:`~src.quality.schemas.VersionRecord`.
        expected_parent_type: The artifact type the expected parent must have.
        expected_parent_id: The artifact ID the expected parent must have.
        check_name: Name recorded on the returned
            :class:`~src.quality.schemas.GateCheckResult`.

    Returns:
        A :class:`~src.quality.schemas.GateCheckResult` — passes only if
        ``(expected_parent_type, expected_parent_id)`` appears among
        *version*'s ``lineage_parents``.
    """
    found = [
        f"{p.parent_artifact_type}:{p.parent_artifact_id}" for p in version.lineage_parents
    ]
    passed = f"{expected_parent_type}:{expected_parent_id}" in found

    if passed:
        detail = (
            f"{version.artifact_type}:{version.artifact_id} lineage includes expected parent "
            f"{expected_parent_type}:{expected_parent_id}"
        )
    else:
        detail = (
            f"{version.artifact_type}:{version.artifact_id} lineage does not include expected "
            f"parent {expected_parent_type}:{expected_parent_id} (found: {found or 'none'})"
        )
    return GateCheckResult(name=check_name, passed=passed, detail=detail)


# ── Dashboard section registration (docs/ADR/0009-engineering-dashboard.md Decision 6) ──

def register_lineage_section() -> None:
    """Register this module's Lineage section into ``dashboard.py``'s ``SECTION_REGISTRY``.

    Called from the bottom of :mod:`src.quality.dashboard` to trigger
    registration — mirroring :func:`src.quality.dataset_metrics.register_dataset_metrics`'s
    import-time registration into ``METRIC_REGISTRY``. Imports
    :mod:`src.quality.dashboard` inside this function body, not at module
    level: dashboard.py also imports *this* module (to call this
    function), so a module-level import here would be a genuine circular
    import — deferring it into this function body breaks the cycle
    without either module needing to guess the other's load order.

    Idempotent — safe to call more than once (mirrors
    :func:`src.quality.dataset_metrics.register_dataset_metrics`).
    """
    from src.quality.dashboard import SECTION_REGISTRY, DashboardSection

    if any(section.name == "lineage" for section in SECTION_REGISTRY.all("dataset")):
        return
    SECTION_REGISTRY.register(
        "dataset",
        DashboardSection(name="lineage", title="Lineage", order=70, render=_render_lineage_section),
    )


def _render_lineage_section(ctx: DashboardContext) -> str:
    """Render ancestor/descendant traversal for the dashboard's Lineage section.

    Formats only what :func:`build_lineage_graph` / :func:`trace_ancestors`
    / :func:`trace_descendants` already computed (ADR-0009 Decision 3) —
    this function derives nothing itself beyond string formatting.

    Args:
        ctx: The dashboard's shared context (uses ``ctx.artifact`` and
            ``ctx.cfg``).

    Returns:
        An HTML fragment (no ``<html>``/``<body>`` wrapper).
    """
    import html as _html

    graph = build_lineage_graph(ctx.cfg)
    ancestors = trace_ancestors(graph, "dataset", ctx.artifact.artifact_id)
    descendants = trace_descendants(graph, "dataset", ctx.artifact.artifact_id)

    def _list_or_empty(nodes: list[LineageNode], empty: str) -> str:
        if not nodes:
            return f"<p>{_html.escape(empty)}</p>"
        items = "".join(
            f"<li>{_html.escape(node.artifact_type)}:{_html.escape(node.artifact_id)}</li>"
            for node in nodes
        )
        return f'<ul class="plain">{items}</ul>'

    ancestors_html = _list_or_empty(ancestors, "No ancestors found.")
    descendants_html = _list_or_empty(
        descendants, "No descendants found (nothing derived from this yet).",
    )
    return (
        "<h2>Lineage</h2>\n"
        f"<h3>Ancestors</h3>{ancestors_html}\n"
        f"<h3>Descendants</h3>{descendants_html}"
    )
