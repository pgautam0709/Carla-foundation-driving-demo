"""
src/quality/coverage.py — Dataset diversity target matrix and gap analysis.

Coverage is defined against a *configured* target matrix (the Cartesian
product of ``quality_engineering.coverage.target_towns`` and
``target_weather``), never inferred from the data itself — see
docs/ADR/0008-coverage-planning.md Decision 1. Recommendations rank unmet
cells deterministically to maximize diversity per additional episode
collected; nothing here is random, and nothing here triggers a collection
run (Decision 3).
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from src.quality.artifacts import DatasetArtifact
from src.quality.config import QualityEngineeringConfig
from src.quality.schemas import (
    QUALITY_SCHEMA_VERSION,
    CoverageCell,
    CoverageRecommendation,
    CoverageResult,
)

#: Default filename written into a dataset directory by :func:`write_coverage_report`.
COVERAGE_REPORT_FILENAME = "coverage_report.json"

#: Human-readable adjectives for common CARLA weather presets, used only
#: to make recommendation messages read naturally (e.g. "rainy Town10").
#: Falls back to the lowercased preset name for anything not listed.
_WEATHER_LABELS: dict[str, str] = {
    "ClearNoon": "clear",
    "CloudyNoon": "cloudy",
    "WetNoon": "wet",
    "WetCloudyNoon": "wet, cloudy",
    "HardRainNoon": "rainy",
    "SoftRainNoon": "lightly rainy",
    "MidRainyNoon": "rainy",
    "ClearSunset": "sunset",
    "CloudySunset": "cloudy sunset",
    "WetSunset": "wet sunset",
    "HardRainSunset": "rainy sunset",
    "ClearNight": "nighttime",
    "CloudyNight": "cloudy nighttime",
    "WetNight": "wet nighttime",
    "HardRainNight": "rainy nighttime",
}


def compute_coverage(artifact: DatasetArtifact, cfg: QualityEngineeringConfig) -> CoverageResult:
    """Compute the ``(town, weather)`` coverage matrix for *artifact*.

    Args:
        artifact: The dataset to analyze.
        cfg: Resolved engineering-loop configuration (uses ``cfg.coverage``).

    Returns:
        A :class:`~src.quality.schemas.CoverageResult` — the full target
        matrix (met and unmet cells alike) plus informational route and
        per-split coverage.
    """
    coverage_cfg = cfg.coverage
    included = [e for e in artifact.episodes if e.included]

    cell_counts: dict[tuple[str, str], int] = {}
    for episode in included:
        if episode.town is not None and episode.weather is not None:
            key = (episode.town, episode.weather)
            cell_counts[key] = cell_counts.get(key, 0) + 1

    cells = [
        CoverageCell(
            town=town, weather=weather,
            episode_count=cell_counts.get((town, weather), 0),
            met=cell_counts.get((town, weather), 0) >= coverage_cfg.min_episodes_per_cell,
        )
        for town, weather in itertools.product(
            coverage_cfg.target_towns, coverage_cfg.target_weather,
        )
    ]
    cells_total = len(cells)
    cells_met = sum(1 for cell in cells if cell.met)
    coverage_pct = 100.0 * cells_met / cells_total if cells_total else 0.0

    routes: dict[str, int] = {}
    for episode in included:
        if episode.route_name is not None:
            routes[episode.route_name] = routes.get(episode.route_name, 0) + 1

    split_coverage: dict[str, dict[str, int]] = {}
    for episode in included:
        if episode.split is None:
            continue
        bucket = split_coverage.setdefault(episode.split, {})
        if episode.town is not None:
            town_key = f"town:{episode.town}"
            bucket[town_key] = bucket.get(town_key, 0) + 1
        if episode.weather is not None:
            weather_key = f"weather:{episode.weather}"
            bucket[weather_key] = bucket.get(weather_key, 0) + 1

    return CoverageResult(
        schema_version=QUALITY_SCHEMA_VERSION,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        artifact_id=artifact.artifact_id,
        target_towns=list(coverage_cfg.target_towns),
        target_weather=list(coverage_cfg.target_weather),
        min_episodes_per_cell=coverage_cfg.min_episodes_per_cell,
        cells=cells,
        cells_met=cells_met,
        cells_total=cells_total,
        coverage_pct=coverage_pct,
        routes=routes,
        split_coverage=split_coverage,
    )


def write_coverage_report(
    dataset_dir: Path, coverage: CoverageResult, filename: str = COVERAGE_REPORT_FILENAME,
) -> Path:
    """Write *coverage* to ``<dataset_dir>/<filename>``.

    Args:
        dataset_dir: The dataset directory to write into.
        coverage: The :class:`~src.quality.schemas.CoverageResult` to persist.
        filename: Output filename, relative to *dataset_dir*.

    Returns:
        The path written to.
    """
    path = Path(dataset_dir) / filename
    path.write_text(json.dumps(coverage.to_dict(), indent=2, default=str), encoding="utf-8")
    return path


def recommend_collection(
    coverage: CoverageResult, cfg: QualityEngineeringConfig,
) -> list[CoverageRecommendation]:
    """Rank unmet coverage cells to maximize diversity gained per episode collected.

    Ranking (docs/ADR/0008-coverage-planning.md Decision 2), all
    deterministic:
        1. Zero-episode cells before under-threshold cells.
        2. Fewer existing episodes first.
        3. Town name, then weather name, alphabetically (final tiebreaker).

    Args:
        coverage: A :class:`~src.quality.schemas.CoverageResult`, typically
            from :func:`compute_coverage`.
        cfg: Resolved engineering-loop configuration (uses
            ``cfg.coverage.max_recommendations``).

    Returns:
        Up to ``cfg.coverage.max_recommendations``
        :class:`~src.quality.schemas.CoverageRecommendation` records, most
        diversity-maximizing first. Empty if every cell is already met.
    """
    unmet = [cell for cell in coverage.cells if not cell.met]
    ranked = sorted(
        unmet,
        key=lambda cell: (cell.episode_count > 0, cell.episode_count, cell.town, cell.weather),
    )
    limit = cfg.coverage.max_recommendations
    return [
        CoverageRecommendation(
            town=cell.town,
            weather=cell.weather,
            current_episode_count=cell.episode_count,
            gap=max(coverage.min_episodes_per_cell - cell.episode_count, 0),
            message=(
                f"Collect additional {_weather_label(cell.weather)} {cell.town} episodes "
                f"({cell.episode_count}/{coverage.min_episodes_per_cell} episodes)"
            ),
        )
        for cell in ranked[:limit]
    ]


def _weather_label(weather: str) -> str:
    """Return a human-readable adjective for a CARLA weather preset name.

    Args:
        weather: CARLA weather preset name, e.g. ``"HardRainNoon"``.

    Returns:
        A lowercase adjective, e.g. ``"rainy"``. Falls back to the
        lowercased preset name if not in :data:`_WEATHER_LABELS`.
    """
    return _WEATHER_LABELS.get(weather, weather.lower())
