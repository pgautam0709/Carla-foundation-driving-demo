"""
src/quality/dashboard.py — Single self-contained HTML view over a dataset's engineering loop.

Generated on demand (``make dashboard``), never a running service
(docs/ADR/0009-engineering-dashboard.md Decision 1) — one static ``.html``
file, all CSS inlined, the Quality Trend chart rendered as inline SVG
built by hand, zero external requests.

Composition-only (ADR-0009 Decision 3): every section formats dataclasses
already computed by :mod:`src.quality.scoring`, :mod:`src.quality.coverage`,
:mod:`src.quality.regression`, :mod:`src.quality.gates`, and
:mod:`src.quality.review` — this module never re-derives a score,
threshold check, or comparison.

Sections are pluggable, keyed by artifact-type category in
:data:`SECTION_REGISTRY` (a :class:`~src.quality.registry.CategoryRegistry`,
the identical class :mod:`src.quality.metrics` uses for
``METRIC_REGISTRY`` — ADR-0004 Decision 6b) — not a fixed template
(ADR-0009 Decision 2). :mod:`src.quality.lineage` registers its own
Lineage section from its own file (ADR-0009 Decision 6); the import at
the bottom of this module triggers that registration, mirroring
:mod:`src.quality.dataset_metrics`'s import-time registration of
``METRIC_REGISTRY``.
"""

from __future__ import annotations

import dataclasses
import html
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from src.quality.artifacts import (
    ARTIFACT_LOAD_ERRORS,
    ArtifactNotFoundError,
    DatasetArtifact,
    load_dataset_artifacts,
    load_quality_score_record,
    load_version_record,
)
from src.quality.config import QualityEngineeringConfig
from src.quality.coverage import compute_coverage, recommend_collection
from src.quality.gates import evaluate_gate
from src.quality.registry import CategoryRegistry
from src.quality.regression import compare_datasets
from src.quality.review import compute_review
from src.quality.schemas import (
    CoverageRecommendation,
    CoverageResult,
    GateReport,
    QualityScore,
    RegressionReport,
    ReviewReport,
    VersionRecord,
)
from src.quality.scoring import compute_quality_score


@dataclasses.dataclass
class DashboardContext:
    """Everything a :class:`DashboardSection` needs to render its fragment.

    Every field is a dataclass already computed by another module —
    sections only format these, never recompute them (ADR-0009 Decision 3).

    Args:
        artifact: The dataset this dashboard describes.
        cfg: Resolved engineering-loop configuration.
        score: Its :class:`~src.quality.schemas.QualityScore`.
        coverage: Its :class:`~src.quality.schemas.CoverageResult`.
        recommendations: Ranked collection recommendations.
        version: Its :class:`~src.quality.schemas.VersionRecord`, or None
            if this dataset has never been versioned (``make version``
            not yet run).
        baseline_artifact_id: ``version.previous_artifact_id`` if that
            dataset's own artifacts could still be loaded, else None —
            recorded separately from ``version`` so sections can tell "no
            previous version" apart from "previous version recorded but
            no longer on disk."
        regression: Comparison against the baseline, or None if there is
            no baseline.
        gate_report: Training-readiness verdict.
        review: Deterministic star review.
        datasets_dir: Parent directory of every dataset build — scanned
            by the Quality Trend section.
    """

    artifact: DatasetArtifact
    cfg: QualityEngineeringConfig
    score: QualityScore
    coverage: CoverageResult
    recommendations: list[CoverageRecommendation]
    version: VersionRecord | None
    baseline_artifact_id: str | None
    regression: RegressionReport | None
    gate_report: GateReport
    review: ReviewReport
    datasets_dir: Path


@dataclasses.dataclass
class DashboardSection:
    """One pluggable dashboard section, registered into :data:`SECTION_REGISTRY`.

    Args:
        name: Registry key (unique within a category) — not shown to the
            reader.
        title: Human-readable heading rendered above this section's HTML.
        order: Sections render in ascending ``order``.
        render: Takes the shared :class:`DashboardContext`, returns an
            HTML fragment (no ``<html>``/``<body>`` wrapper).
    """

    name: str
    title: str
    order: int
    render: Callable[[DashboardContext], str]


#: The identical CategoryRegistry class metrics.py::METRIC_REGISTRY uses
#: (ADR-0004 Decision 6b), instantiated here for dashboard sections
#: (ADR-0009 Decision 2).
SECTION_REGISTRY: CategoryRegistry[DashboardSection] = CategoryRegistry()


# ── Public entry point ───────────────────────────────────────────────────────────

def generate_dashboard(
    dataset_dir: Path,
    cfg: QualityEngineeringConfig,
    *,
    datasets_dir: Path | None = None,
) -> Path:
    """Generate and write the self-contained HTML dashboard for one dataset.

    Args:
        dataset_dir: The dataset directory to build a dashboard for.
        cfg: Resolved engineering-loop configuration.
        datasets_dir: Parent directory of every dataset build, scanned by
            the Quality Trend section. Defaults to ``dataset_dir.parent``.

    Returns:
        Path to the written ``.html`` file (``<output_dir>/<dataset_id>_dashboard.html``).
    """
    dataset_dir = Path(dataset_dir)
    resolved_datasets_dir = Path(datasets_dir) if datasets_dir is not None else dataset_dir.parent

    artifact = load_dataset_artifacts(dataset_dir)

    try:
        version = load_version_record(dataset_dir)
    except ArtifactNotFoundError:
        version = None

    baseline: DatasetArtifact | None = None
    baseline_artifact_id: str | None = None
    if version is not None and version.previous_artifact_id is not None:
        baseline_artifact_id = version.previous_artifact_id
        baseline_dir = dataset_dir.parent / version.previous_artifact_id
        try:
            baseline = load_dataset_artifacts(baseline_dir)
        except ARTIFACT_LOAD_ERRORS:
            baseline = None

    score = compute_quality_score(artifact, cfg)
    coverage = compute_coverage(artifact, cfg)
    recommendations = recommend_collection(coverage, cfg)
    regression = compare_datasets(baseline, artifact, cfg) if baseline is not None else None
    gate_report = evaluate_gate(artifact, cfg, baseline=baseline)
    review = compute_review(artifact, cfg, baseline=baseline)

    artifact_type = version.artifact_type if version is not None else "dataset"

    context = DashboardContext(
        artifact=artifact,
        cfg=cfg,
        score=score,
        coverage=coverage,
        recommendations=recommendations,
        version=version,
        baseline_artifact_id=baseline_artifact_id,
        regression=regression,
        gate_report=gate_report,
        review=review,
        datasets_dir=resolved_datasets_dir,
    )

    sections = sorted(SECTION_REGISTRY.all(artifact_type), key=lambda s: s.order)
    body = "\n".join(f'<section class="card">\n{s.render(context)}\n</section>' for s in sections)
    page = _wrap_page(artifact.artifact_id, body)

    output_dir = Path(cfg.dashboard.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{artifact.artifact_id}_dashboard.html"
    output_path.write_text(page, encoding="utf-8")
    return output_path


# ── Section: Header ──────────────────────────────────────────────────────────────

def _render_header(ctx: DashboardContext) -> str:
    """Render dataset identity: ID, timestamps, git commit, top-line counts."""
    a = ctx.artifact
    stars_html = "&#9733;" * ctx.review.stars + "&#9734;" * (5 - ctx.review.stars)
    rows = [
        ("Dataset ID", _esc(a.artifact_id)),
        ("Built at", _esc(a.created_at or "unknown")),
        ("Git commit", _esc(a.git_commit or "unknown")),
        ("Episodes included", str(a.quality_report.episodes_included)),
        ("Samples", str(a.manifest.sample_count)),
        ("Grade", f"{_esc(ctx.score.grade)} ({stars_html} {ctx.review.stars}/5)"),
    ]
    return (
        f"<h1>Dataset Engineering Report — {_esc(a.artifact_id)}</h1>\n"
        + _table(rows)
    )


# ── Section: Quality ──────────────────────────────────────────────────────────────

def _render_quality(ctx: DashboardContext) -> str:
    """Render the overall score, grade, and per-metric breakdown."""
    header = (
        f"<h2>Quality — {ctx.score.overall_score:.1f}/100 "
        f"(grade {_esc(ctx.score.grade)})</h2>"
    )
    metric_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{result.raw_score:.1f}</td>"
        f"<td>{ctx.score.weights_used.get(name, 0.0):.2f}</td><td>{_esc(result.detail)}</td></tr>"
        for name, result in sorted(ctx.score.metrics.items())
    )
    metrics_table = (
        '<table><thead><tr><th>Metric</th><th>Score</th><th>Weight</th>'
        f"<th>Detail</th></tr></thead><tbody>{metric_rows}</tbody></table>"
    )
    strengths = _list(ctx.review.strengths, empty="No notable strengths.")
    weaknesses = _list(ctx.review.weaknesses, empty="No notable weaknesses.")
    return (
        f"{header}\n{metrics_table}\n"
        f"<h3>Strengths</h3>{strengths}\n<h3>Weaknesses</h3>{weaknesses}"
    )


# ── Section: Coverage ─────────────────────────────────────────────────────────────

def _render_coverage(ctx: DashboardContext) -> str:
    """Render the coverage matrix summary and ranked collection recommendations."""
    c = ctx.coverage
    header = f"<h2>Coverage — {c.cells_met}/{c.cells_total} cells met ({c.coverage_pct:.1f}%)</h2>"
    unmet = [cell for cell in c.cells if not cell.met]
    unmet_rows = "".join(
        f"<tr><td>{_esc(cell.town)}</td><td>{_esc(cell.weather)}</td>"
        f"<td>{cell.episode_count}/{c.min_episodes_per_cell}</td></tr>"
        for cell in unmet
    )
    unmet_table = (
        "<h3>Unmet cells</h3>"
        + (
            '<table><thead><tr><th>Town</th><th>Weather</th><th>Episodes</th>'
            f"</tr></thead><tbody>{unmet_rows}</tbody></table>"
            if unmet
            else "<p>All target cells met.</p>"
        )
    )
    recs = _list(
        [rec.message for rec in ctx.recommendations], empty="No recommendations — coverage met.",
    )
    return f"{header}\n{unmet_table}\n<h3>Recommendations</h3>{recs}"


# ── Section: Validation Gate ────────────────────────────────────────────────────

def _render_validation(ctx: DashboardContext) -> str:
    """Render every configured gate check's pass/fail status and reason.

    Always shows every check, including failures when the overall verdict
    is "pass" and passes when the overall verdict is "fail" — ADR-0009
    Decision 5: a reviewer needs to know exactly which check blocked
    training, not just a single badge.
    """
    verdict = "PASS" if ctx.gate_report.passed else "FAIL"
    verdict_class = "pass" if ctx.gate_report.passed else "fail"
    header = f'<h2>Training Readiness — <span class="badge {verdict_class}">{verdict}</span></h2>'
    rows = "".join(
        f'<tr><td><span class="badge {"pass" if c.passed else "fail"}">'
        f'{"PASS" if c.passed else "FAIL"}</span></td>'
        f"<td>{_esc(c.name)}</td><td>{_esc(c.detail)}</td></tr>"
        for c in ctx.gate_report.checks
    )
    table = (
        '<table><thead><tr><th>Status</th><th>Check</th><th>Detail</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )
    return f"{header}\n{table}"


# ── Section: Recent Changes ─────────────────────────────────────────────────────

def _render_recent_changes(ctx: DashboardContext) -> str:
    """Render version identity and, if a baseline exists, its regression findings."""
    if ctx.version is None:
        return (
            "<h2>Recent Changes</h2>"
            "<p>This dataset has not been versioned yet (run <code>make version</code>).</p>"
        )

    version_rows = [
        ("Artifact ID", _esc(ctx.version.artifact_id)),
        ("Previous version", _esc(ctx.version.previous_artifact_id or "(none — first version)")),
        ("Versioned at", _esc(ctx.version.created_at)),
        ("Generator version", _esc(ctx.version.generator_version)),
    ]
    header = f"<h2>Recent Changes</h2>\n{_table(version_rows)}"

    if ctx.regression is None:
        note = (
            "<p>No comparison available"
            + (f" (baseline {_esc(ctx.baseline_artifact_id)} not found on disk)."
               if ctx.baseline_artifact_id else ".")
            + "</p>"
        )
        return f"{header}\n{note}"

    finding_rows = "".join(
        f'<tr><td><span class="badge {_severity_class(f.severity)}">{_esc(f.severity)}</span></td>'
        f"<td>{_esc(f.dimension)}</td><td>{_esc(f.message)}</td></tr>"
        for f in ctx.regression.findings
        if f.severity != "informational"
    )
    findings_table = (
        "<h3>Findings vs previous version</h3>"
        + (
            '<table><thead><tr><th>Severity</th><th>Dimension</th><th>Message</th></tr></thead>'
            f"<tbody>{finding_rows}</tbody></table>"
            if finding_rows
            else "<p>No warnings, failures, or improvements — everything within threshold.</p>"
        )
    )
    return f"{header}\n{findings_table}"


# ── Section: Quality Trend ──────────────────────────────────────────────────────

def _render_quality_trend(ctx: DashboardContext) -> str:
    """Render a hand-built inline SVG line chart plus a table, over historical scores."""
    history = _scan_trend(ctx.datasets_dir, ctx.cfg)
    header = "<h2>Quality Trend</h2>"
    if not history:
        return (
            f"{header}<p>No versioned + scored dataset history found under "
            f"{_esc(str(ctx.datasets_dir))}.</p>"
        )

    svg = _trend_svg(history)
    rows = "".join(
        f"<tr><td>{_esc(artifact_id)}</td><td>{_esc(created_at)}</td><td>{overall_score:.1f}</td></tr>"
        for artifact_id, created_at, overall_score in history
    )
    table = (
        '<table><thead><tr><th>Dataset</th><th>Built at</th><th>Score</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )
    return f"{header}\n{svg}\n{table}"


def _scan_trend(
    datasets_dir: Path, cfg: QualityEngineeringConfig,
) -> list[tuple[str, str, float]]:
    """Collect ``(artifact_id, created_at, overall_score)`` for every versioned+scored dataset.

    Args:
        datasets_dir: Parent directory of every dataset build.
        cfg: Resolved engineering-loop configuration (uses
            ``cfg.dashboard.trend_window``).

    Returns:
        Rows sorted by ``created_at`` ascending, trimmed to the most
        recent ``cfg.dashboard.trend_window`` entries. Empty if
        *datasets_dir* does not exist or no dataset has both a
        ``version.json`` and a ``quality_score.json``.
    """
    if not datasets_dir.is_dir():
        return []
    rows: list[tuple[str, str, float]] = []
    for candidate in sorted(p for p in datasets_dir.iterdir() if p.is_dir()):
        try:
            version = load_version_record(candidate)
            score = load_quality_score_record(candidate)
        except ARTIFACT_LOAD_ERRORS:
            continue
        rows.append((version.artifact_id, version.created_at, score.overall_score))
    rows.sort(key=lambda r: r[1])
    window = cfg.dashboard.trend_window
    return rows[-window:] if window > 0 else rows


def _trend_svg(history: list[tuple[str, str, float]]) -> str:
    """Render *history* as a minimal hand-built ``<svg>`` polyline, 0-100 y-axis.

    Args:
        history: ``(artifact_id, created_at, overall_score)`` rows, oldest
            first.

    Returns:
        A complete ``<svg>...</svg>`` string. A single point renders as a
        lone circle with no line.
    """
    width, height, pad = 480, 160, 24
    plot_w, plot_h = width - 2 * pad, height - 2 * pad
    n = len(history)

    def _xy(i: int, score: float) -> tuple[float, float]:
        x = pad if n <= 1 else pad + (i / (n - 1)) * plot_w
        y = pad + (1 - score / 100.0) * plot_h
        return x, y

    points = [_xy(i, score) for i, (_id, _t, score) in enumerate(history)]
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    circles = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" class="trend-point" />' for x, y in points
    )
    baseline_y = pad + plot_h
    svg_open = (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'class="trend-chart">'
    )
    axis_line = (
        f'<line x1="{pad}" y1="{baseline_y:.1f}" x2="{width - pad}" y2="{baseline_y:.1f}" '
        'class="trend-axis" />'
    )
    return (
        f"{svg_open}"
        f"{axis_line}"
        f'<polyline points="{polyline}" class="trend-line" />'
        f"{circles}"
        "</svg>"
    )


# ── Section registration (dashboard.py's own six sections, category "dataset") ──

SECTION_REGISTRY.register(
    "dataset", DashboardSection(name="header", title="Header", order=10, render=_render_header),
)
SECTION_REGISTRY.register(
    "dataset", DashboardSection(name="quality", title="Quality", order=20, render=_render_quality),
)
SECTION_REGISTRY.register(
    "dataset",
    DashboardSection(name="coverage", title="Coverage", order=30, render=_render_coverage),
)
SECTION_REGISTRY.register(
    "dataset",
    DashboardSection(
        name="validation", title="Validation Gate", order=40, render=_render_validation,
    ),
)
SECTION_REGISTRY.register(
    "dataset",
    DashboardSection(
        name="recent_changes", title="Recent Changes", order=50, render=_render_recent_changes,
    ),
)
SECTION_REGISTRY.register(
    "dataset",
    DashboardSection(
        name="quality_trend", title="Quality Trend", order=60, render=_render_quality_trend,
    ),
)


# ── HTML shell / small formatting helpers ───────────────────────────────────────

_CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2rem auto;
       max-width: 960px; color: #1a1a1a; background: #fafafa; }
h1 { font-size: 1.5rem; } h2 { font-size: 1.2rem; margin-top: 0; } h3 { font-size: 1rem; }
.card { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 1.25rem 1.5rem;
        margin-bottom: 1.25rem; }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #eee;
         font-size: 0.9rem; }
th { background: #f2f2f2; }
code { background: #f2f2f2; padding: 0.1rem 0.3rem; border-radius: 3px; }
.badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 4px; font-weight: 600;
         font-size: 0.8rem; color: #fff; }
.badge.pass { background: #2e7d32; }
.badge.fail { background: #c62828; }
.badge.failure { background: #c62828; }
.badge.warning { background: #f9a825; color: #1a1a1a; }
.badge.improvement { background: #2e7d32; }
.badge.informational { background: #757575; }
.trend-chart { background: #fff; }
.trend-axis { stroke: #ccc; stroke-width: 1; }
.trend-line { fill: none; stroke: #1565c0; stroke-width: 2; }
.trend-point { fill: #1565c0; }
ul.plain { margin: 0.25rem 0; padding-left: 1.25rem; }
"""


def _wrap_page(artifact_id: str, body: str) -> str:
    """Wrap rendered section fragments in the full self-contained HTML document.

    Args:
        artifact_id: The dataset this dashboard describes (used in
            ``<title>``).
        body: Concatenated section HTML fragments.

    Returns:
        A complete ``<!DOCTYPE html>...`` document with CSS inlined,
        zero external requests.
    """
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>Dataset Report — {_esc(artifact_id)}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n{body}\n"
        f'<p><small>Generated {_esc(generated_at)}</small></p>\n</body>\n</html>\n'
    )


def _esc(value: str) -> str:
    """Escape *value* for safe embedding in HTML text/attribute content."""
    return html.escape(str(value), quote=True)


def _table(rows: list[tuple[str, str]]) -> str:
    """Render a two-column ``(label, value)`` HTML table. *value* is pre-escaped by callers."""
    body = "".join(f"<tr><th>{_esc(label)}</th><td>{value}</td></tr>" for label, value in rows)
    return f"<table>{body}</table>"


def _list(items: list[str], *, empty: str) -> str:
    """Render *items* as an HTML bullet list, or a plain paragraph if empty."""
    if not items:
        return f"<p>{_esc(empty)}</p>"
    return '<ul class="plain">' + "".join(f"<li>{_esc(item)}</li>" for item in items) + "</ul>"


def _severity_class(severity: str) -> str:
    """Map a :class:`~src.quality.schemas.RegressionFinding` severity to a CSS badge class."""
    known = ("failure", "warning", "improvement", "informational")
    return severity if severity in known else "informational"


# ── Trigger Lineage section registration (ADR-0009 Decision 6) ──────────────────
# lineage.py registers its own LineageSection into SECTION_REGISTRY above, under
# category "dataset" — importing and calling it here is the side-effect trigger,
# mirroring dataset_metrics.py's import-time registration into METRIC_REGISTRY
# (see docs/ADR/0009-engineering-dashboard.md Decision 6's documented consequence).
from src.quality.lineage import register_lineage_section  # noqa: E402

register_lineage_section()
