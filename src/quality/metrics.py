"""
src/quality/metrics.py — Metric interface and the shared category registry.

Concrete metrics (:mod:`src.quality.dataset_metrics` today; a future
``model_metrics.py`` / ``simulation_metrics.py`` / ``deployment_metrics.py``)
register themselves into :data:`METRIC_REGISTRY` under a category string at
import time. :mod:`src.quality.scoring` queries the registry by category —
it has no per-category knowledge of which concrete metrics exist.
"""

from __future__ import annotations

import abc

from src.quality.config import QualityEngineeringConfig
from src.quality.registry import CategoryRegistry
from src.quality.schemas import Artifact, MetricResult


class Metric(abc.ABC):
    """A named, weighted, artifact-scoped quality signal.

    Subclasses are registered into :data:`METRIC_REGISTRY` under a
    category string (e.g. ``"dataset"``). A category's metrics always
    receive the matching concrete :class:`~src.quality.schemas.Artifact`
    subtype for that category (e.g. every ``"dataset"`` metric receives a
    :class:`~src.quality.artifacts.DatasetArtifact`) — a documented
    convention, enforced by each metric's own ``isinstance`` narrowing and
    exercised by this package's tests, not by the type system (see
    docs/ADR/0004-engineering-loop-architecture.md Decision 6b).

    Attributes:
        name: Stable identifier, unique within a category — used as the
            registry key.
    """

    name: str

    @abc.abstractmethod
    def compute(self, artifact: Artifact, cfg: QualityEngineeringConfig) -> MetricResult:
        """Compute this metric's score for *artifact*.

        Args:
            artifact: The artifact to score. Concrete subclasses narrow
                this to their category's specific subtype.
            cfg: Resolved engineering-loop configuration.

        Returns:
            A :class:`~src.quality.schemas.MetricResult`.

        Raises:
            TypeError: If *artifact* is not the subtype this metric's
                category expects.
        """
        raise NotImplementedError


#: One process-wide registry, shared with
#: :data:`src.quality.dashboard.SECTION_REGISTRY` via the same
#: :class:`~src.quality.registry.CategoryRegistry` implementation.
METRIC_REGISTRY: CategoryRegistry[Metric] = CategoryRegistry()
