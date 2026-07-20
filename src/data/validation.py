"""
src/data/validation.py — Episode validator for Phase 2.

Validates a collected episode directory without requiring CARLA, Docker,
or any external service.  Suitable for use in CI and post-collection checks.

Usage::

    from pathlib import Path
    from src.data.validation import EpisodeValidator

    result = EpisodeValidator().validate(Path("data/raw/episodes/episode_..."))
    if result.valid:
        print("Episode is valid")
    else:
        for error in result.errors:
            print(f"  Error: {error}")
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from src.utils.logging import get_logger

log = get_logger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────────

#: Files that must be present in every episode directory.
REQUIRED_FILES: list[str] = [
    "metadata.json",
    "route.json",
    "controls.jsonl",
    "telemetry.jsonl",
    "events.jsonl",
    "manifest.json",
]

#: Top-level keys required in metadata.json.
REQUIRED_METADATA_FIELDS: list[str] = [
    "episode_id",
    "created_at",
    "schema_version",
    "runtime_profile",
    "carla_host",
    "carla_port",
    "town",
    "tick_count_target",
    "collection_mode",
]

#: Top-level keys required in manifest.json.
REQUIRED_MANIFEST_FIELDS: list[str] = [
    "episode_id",
    "schema_version",
    "files",
    "frame_count",
    "control_row_count",
    "telemetry_row_count",
    "event_count",
    "status",
    "validation_status",
]


# ── Result types ───────────────────────────────────────────────────────────────

@dataclasses.dataclass
class CheckResult:
    """Result of a single validation check.

    Args:
        name: Human-readable check identifier.
        passed: True if the check passed.
        detail: One-line explanation (present/missing/error message).
    """

    name: str
    passed: bool
    detail: str


@dataclasses.dataclass
class ValidationResult:
    """Aggregated result of all validation checks for one episode.

    Args:
        episode_id: Episode identifier (directory basename).
        valid: True only if every check passed and errors is empty.
        checks: Ordered list of individual :class:`CheckResult` objects.
        errors: Human-readable list of failures (empty when valid).
    """

    episode_id: str
    valid: bool
    checks: list[CheckResult]
    errors: list[str]


# ── Validator ──────────────────────────────────────────────────────────────────

class EpisodeValidator:
    """Validates a collected episode directory.

    Checks performed:
    - All required files are present.
    - All JSONL files are parseable (every line is valid JSON).
    - ``metadata.json`` has all required fields.
    - ``manifest.json`` has all required fields.
    - ``telemetry.jsonl`` is non-empty.
    - Frame filenames are sequential (``000000.png``, ``000001.png``, …).
    - Frame count is reported alongside control row count.

    All checks are informational — no check has external side effects.
    """

    def validate(self, episode_dir: Path) -> ValidationResult:
        """Run all checks against the given episode directory.

        Args:
            episode_dir: Absolute or relative path to the episode root.
                The directory must exist.

        Returns:
            A :class:`ValidationResult` with every check result and an
            ``errors`` list summarising failures.
        """
        episode_dir = Path(episode_dir)
        episode_id = episode_dir.name
        checks: list[CheckResult] = []
        errors: list[str] = []

        log.info("validator.start", episode_id=episode_id)

        # ── Required file presence ─────────────────────────────────────────────
        for fname in REQUIRED_FILES:
            path = episode_dir / fname
            exists = path.exists()
            checks.append(CheckResult(
                name=f"file: {fname}",
                passed=exists,
                detail="present" if exists else f"MISSING: {path}",
            ))
            if not exists:
                errors.append(f"Missing required file: {fname}")

        # ── JSONL parseability ─────────────────────────────────────────────────
        for fname in ("controls.jsonl", "telemetry.jsonl", "events.jsonl"):
            path = episode_dir / fname
            if path.exists():
                result = self._check_jsonl(path)
                checks.append(result)
                if not result.passed:
                    errors.append(result.detail)

        # ── Metadata fields ────────────────────────────────────────────────────
        metadata_path = episode_dir / "metadata.json"
        if metadata_path.exists():
            result = self._check_json_fields(
                metadata_path,
                REQUIRED_METADATA_FIELDS,
                label="metadata fields",
            )
            checks.append(result)
            if not result.passed:
                errors.append(result.detail)

        # ── Manifest fields ────────────────────────────────────────────────────
        manifest_path = episode_dir / "manifest.json"
        if manifest_path.exists():
            result = self._check_json_fields(
                manifest_path,
                REQUIRED_MANIFEST_FIELDS,
                label="manifest fields",
            )
            checks.append(result)
            if not result.passed:
                errors.append(result.detail)

        # ── Telemetry non-empty ────────────────────────────────────────────────
        telem_path = episode_dir / "telemetry.jsonl"
        if telem_path.exists():
            row_count = _count_jsonl_rows(telem_path)
            passed = row_count > 0
            checks.append(CheckResult(
                name="telemetry non-empty",
                passed=passed,
                detail=f"{row_count} rows" if passed else "0 rows — no telemetry recorded",
            ))
            if not passed:
                errors.append("telemetry.jsonl has no rows")

        # ── Frame sequencing ───────────────────────────────────────────────────
        seq_result = self._check_frame_sequential(episode_dir)
        checks.append(seq_result)
        if not seq_result.passed:
            errors.append(seq_result.detail)

        # ── Frame vs control count ─────────────────────────────────────────────
        checks.append(self._check_frame_count(episode_dir))

        valid = len(errors) == 0
        log.info(
            "validator.done",
            episode_id=episode_id,
            valid=valid,
            errors=len(errors),
        )
        return ValidationResult(
            episode_id=episode_id,
            valid=valid,
            checks=checks,
            errors=errors,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _check_jsonl(path: Path) -> CheckResult:
        """Verify every non-empty line in a JSONL file is valid JSON.

        Args:
            path: Path to the ``.jsonl`` file.

        Returns:
            A :class:`CheckResult` with parse status.
        """
        try:
            raw = path.read_text(encoding="utf-8")
            rows = [line for line in raw.splitlines() if line.strip()]
            for _i, line in enumerate(rows):
                json.loads(line)
            return CheckResult(
                name=f"jsonl parseable: {path.name}",
                passed=True,
                detail=f"{len(rows)} rows",
            )
        except json.JSONDecodeError as exc:
            return CheckResult(
                name=f"jsonl parseable: {path.name}",
                passed=False,
                detail=f"JSON parse error on line: {exc}",
            )
        except OSError as exc:
            return CheckResult(
                name=f"jsonl parseable: {path.name}",
                passed=False,
                detail=f"Read error: {exc}",
            )

    @staticmethod
    def _check_json_fields(
        path: Path,
        required: list[str],
        label: str,
    ) -> CheckResult:
        """Check that a JSON file has all required top-level fields.

        Args:
            path: Path to the ``.json`` file.
            required: List of required top-level key names.
            label: Check label for the result.

        Returns:
            A :class:`CheckResult`.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return CheckResult(name=label, passed=False, detail=f"Read/parse error: {exc}")

        missing = [f for f in required if f not in data]
        if missing:
            return CheckResult(
                name=label,
                passed=False,
                detail=f"Missing fields: {missing}",
            )
        return CheckResult(
            name=label,
            passed=True,
            detail=f"All {len(required)} required fields present",
        )

    @staticmethod
    def _check_frame_sequential(episode_dir: Path) -> CheckResult:
        """Verify frame filenames are ``000000.png``, ``000001.png``, etc.

        Args:
            episode_dir: Episode root directory.

        Returns:
            A :class:`CheckResult`.
        """
        camera_dir = episode_dir / "frames" / "front_camera"
        if not camera_dir.exists():
            return CheckResult(
                name="frame sequential",
                passed=True,
                detail="frames/front_camera/ absent — no frames expected",
            )

        frames = sorted(camera_dir.glob("*.png"))
        if not frames:
            return CheckResult(
                name="frame sequential",
                passed=True,
                detail="0 frames (dry-run or no data collected)",
            )

        for i, frame in enumerate(frames):
            expected = f"{i:06d}.png"
            if frame.name != expected:
                return CheckResult(
                    name="frame sequential",
                    passed=False,
                    detail=f"Expected {expected!r} at index {i}, found {frame.name!r}",
                )

        return CheckResult(
            name="frame sequential",
            passed=True,
            detail=f"{len(frames)} frames — sequential ✓",
        )

    @staticmethod
    def _check_frame_count(episode_dir: Path) -> CheckResult:
        """Report frame count relative to control row count.

        Note:
            A mismatch is **not** treated as a failure — partial episodes are
            valid.  This check is informational only.

        Args:
            episode_dir: Episode root directory.

        Returns:
            Always a passing :class:`CheckResult` with count information.
        """
        camera_dir = episode_dir / "frames" / "front_camera"
        frame_count = len(list(camera_dir.glob("*.png"))) if camera_dir.exists() else 0

        controls_path = episode_dir / "controls.jsonl"
        if controls_path.exists():
            control_rows = _count_jsonl_rows(controls_path)
            return CheckResult(
                name="frame/control counts",
                passed=True,
                detail=f"{frame_count} frames, {control_rows} control rows",
            )

        return CheckResult(
            name="frame/control counts",
            passed=True,
            detail=f"{frame_count} frames (controls.jsonl absent)",
        )


# ── Manifest fixup (Phase 3b) ────────────────────────────────────────────────────

def write_validation_status(episode_dir: Path, valid: bool) -> None:
    """Write a validation outcome back into ``manifest.json``'s ``validation_status``.

    Collection always writes ``validation_status: "unchecked"`` (see
    :meth:`~src.data.writers.EpisodeWriter.finalize_manifest`) — this is the
    Phase 3 follow-through referenced in ``docs/PHASE2_DATA_COLLECTION.md``
    (ADR-003) that updates it once an episode has actually been validated.

    Args:
        episode_dir: Episode root directory.
        valid: The :attr:`ValidationResult.valid` outcome to record —
            written as ``"valid"`` or ``"invalid"``.

    Raises:
        FileNotFoundError: If ``manifest.json`` does not exist in
            *episode_dir* — there is nothing to fix.
    """
    manifest_path = episode_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found: {manifest_path}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["validation_status"] = "valid" if valid else "invalid"
    manifest_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    log.info("validator.manifest_fixed", episode_id=episode_dir.name,
             validation_status=data["validation_status"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _count_jsonl_rows(path: Path) -> int:
    """Count non-empty lines in a JSONL file without full parsing.

    Args:
        path: Path to the ``.jsonl`` file.

    Returns:
        Number of non-empty lines.
    """
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0
