#!/usr/bin/env python3
"""
scripts/diagnose.py — Dependency health check and environment diagnostics.

Self-contained: no project imports required. Can be run before `make setup`.

Usage::

    python scripts/diagnose.py
    python scripts/diagnose.py --profile linux_gpu
    python scripts/diagnose.py --strict   # exit 1 on any FAIL

Exit codes:
    0  All critical checks passed (WARNs are acceptable)
    1  One or more critical checks FAILED
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ── Minimal colour support (no dependencies) ───────────────────────────────────
_NO_COLOUR = not sys.stdout.isatty() or os.environ.get("NO_COLOR")


def _c(text: str, code: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


GREEN  = lambda t: _c(t, "32")  # noqa: E731
YELLOW = lambda t: _c(t, "33")  # noqa: E731
RED    = lambda t: _c(t, "31")  # noqa: E731
BOLD   = lambda t: _c(t, "1")   # noqa: E731
DIM    = lambda t: _c(t, "2")   # noqa: E731
CYAN   = lambda t: _c(t, "36")  # noqa: E731


# ── Result model ───────────────────────────────────────────────────────────────

class Status(Enum):
    OK   = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    fix: str = ""
    critical: bool = True


@dataclass
class DiagnosticsReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def has_failures(self) -> bool:
        return any(r.status == Status.FAIL and r.critical for r in self.results)

    @property
    def summary(self) -> tuple[int, int, int, int]:
        ok   = sum(1 for r in self.results if r.status == Status.OK)
        warn = sum(1 for r in self.results if r.status == Status.WARN)
        fail = sum(1 for r in self.results if r.status == Status.FAIL)
        skip = sum(1 for r in self.results if r.status == Status.SKIP)
        return ok, warn, fail, skip


# ── Individual checks ──────────────────────────────────────────────────────────

def check_python_version() -> CheckResult:
    vi = sys.version_info
    version_str = f"{vi.major}.{vi.minor}.{vi.micro}"
    if vi >= (3, 10):
        return CheckResult("Python ≥ 3.10", Status.OK, f"Python {version_str}")
    return CheckResult(
        "Python ≥ 3.10", Status.FAIL,
        f"Found Python {version_str}",
        "Install Python 3.10: https://www.python.org/downloads/  "
        "or use pyenv: pyenv install 3.10",
    )


def check_git() -> CheckResult:
    git = shutil.which("git")
    if git is None:
        return CheckResult(
            "git", Status.FAIL,
            "git not found in PATH",
            "Install git: https://git-scm.com/downloads",
        )
    result = subprocess.run(["git", "--version"], capture_output=True, text=True)
    return CheckResult("git", Status.OK, result.stdout.strip())


def check_uv() -> CheckResult:
    uv = shutil.which("uv")
    if uv is None:
        return CheckResult(
            "uv (package manager)", Status.WARN,
            "uv not found — pip will be used as fallback",
            "Install uv: curl -Ls https://astral.sh/uv/install.sh | sh",
            critical=False,
        )
    result = subprocess.run(["uv", "--version"], capture_output=True, text=True)
    return CheckResult("uv (package manager)", Status.OK, result.stdout.strip())


def check_package(
    import_name: str,
    display_name: str | None = None,
    min_version: str | None = None,
    critical: bool = True,
    install_hint: str | None = None,
) -> CheckResult:
    name = display_name or import_name
    spec = importlib.util.find_spec(import_name)
    if spec is None:
        fix = install_hint or f"uv pip install {import_name}"
        return CheckResult(name, Status.FAIL if critical else Status.WARN,
                           "Not installed", fix, critical=critical)

    try:
        mod = importlib.import_module(import_name)
        version = getattr(mod, "__version__", "unknown")
    except Exception as exc:
        return CheckResult(name, Status.WARN, f"Importable but error: {exc}")

    detail = f"v{version}"
    if min_version:
        from packaging.version import Version  # type: ignore[import]
        try:
            if Version(version) < Version(min_version):
                return CheckResult(
                    name, Status.WARN,
                    f"v{version} (need ≥ {min_version})",
                    f"uv pip install --upgrade {import_name}",
                    critical=False,
                )
        except Exception:
            pass  # packaging not available — skip version check

    return CheckResult(name, Status.OK, detail, critical=critical)


def check_carla() -> CheckResult:
    spec = importlib.util.find_spec("carla")
    if spec is None:
        return CheckResult(
            "carla Python package", Status.WARN,
            "Not installed — simulation requires CARLA wheel",
            "Download CARLA 0.9.15 from https://github.com/carla-simulator/carla/releases\n"
            "    Then: pip install <CARLA_ROOT>/PythonAPI/carla/dist/carla-0.9.15-cp310-*.whl",
            critical=False,
        )
    try:
        import carla  # type: ignore[import]
        version = getattr(carla, "__version__", "unknown")
        return CheckResult("carla Python package", Status.OK, f"v{version}")
    except Exception as exc:
        return CheckResult(
            "carla Python package", Status.WARN,
            f"Found but failed to import: {exc}",
            critical=False,
        )


def check_carla_server(
    host: str = "localhost", port: int = 2000, timeout: float = 3.0
) -> CheckResult:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return CheckResult(
                f"CARLA server ({host}:{port})", Status.OK,
                "Connection successful",
            )
    except (OSError, ConnectionRefusedError, TimeoutError):
        return CheckResult(
            f"CARLA server ({host}:{port})", Status.WARN,
            f"Not reachable at {host}:{port}",
            "Start CARLA: ./CarlaUE4.sh  or  ./CarlaUE4.sh -RenderOffScreen",
            critical=False,
        )


def check_cuda() -> CheckResult:
    spec = importlib.util.find_spec("torch")
    if spec is None:
        return CheckResult(
            "CUDA (via PyTorch)", Status.SKIP,
            "PyTorch not installed — skipping CUDA check",
            critical=False,
        )
    try:
        import torch  # type: ignore[import]
        if torch.cuda.is_available():
            n = torch.cuda.device_count()
            name = torch.cuda.get_device_name(0) if n > 0 else "unknown"
            return CheckResult(
                "CUDA", Status.OK,
                f"{n} device(s) — {name}  (CUDA {torch.version.cuda})",
            )
        # Check MPS (Apple Silicon)
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return CheckResult("MPS (Apple Silicon)", Status.OK, "Available")
        return CheckResult(
            "CUDA / MPS", Status.WARN,
            "No GPU acceleration found — training will use CPU",
            "Install CUDA 11.8+: https://developer.nvidia.com/cuda-downloads",
            critical=False,
        )
    except Exception as exc:
        return CheckResult("CUDA", Status.WARN, f"Check failed: {exc}", critical=False)


def check_data_dirs() -> list[CheckResult]:
    results = []
    root = Path(__file__).resolve().parents[1]
    for subdir in ["data/raw", "data/processed", "outputs"]:
        path = root / subdir
        try:
            path.mkdir(parents=True, exist_ok=True)
            # Write test
            test_file = path / ".write_test"
            test_file.touch()
            test_file.unlink()
            results.append(CheckResult(f"dir: {subdir}", Status.OK, str(path)))
        except PermissionError:
            results.append(CheckResult(
                f"dir: {subdir}", Status.FAIL,
                f"Not writable: {path}",
                f"chmod u+w {path}",
            ))
    return results


def check_env_vars() -> list[CheckResult]:
    results = []
    optional_vars = {
        "CARLA_ROOT": "Path to CARLA installation (optional, for wheel install)",
        "CUDA_VISIBLE_DEVICES": "GPU selection (optional)",
    }
    for var, description in optional_vars.items():
        val = os.environ.get(var)
        if val:
            results.append(CheckResult(f"env: {var}", Status.OK, val, critical=False))
        else:
            results.append(CheckResult(
                f"env: {var}", Status.SKIP,
                f"Not set — {description}",
                critical=False,
            ))
    return results


# ── Rendering ──────────────────────────────────────────────────────────────────

def _status_badge(status: Status) -> str:
    mapping = {
        Status.OK:   GREEN("[ OK   ]"),
        Status.WARN: YELLOW("[ WARN ]"),
        Status.FAIL: RED("[FAIL  ]"),
        Status.SKIP: DIM("[ SKIP ]"),
    }
    return mapping[status]


def print_report(report: DiagnosticsReport) -> None:
    width = 72
    print()
    print(BOLD("─" * width))
    print(BOLD(CYAN("  CARLA Foundation Driving Demo — Environment Diagnostics")))
    print(BOLD("─" * width))
    print(DIM(f"  Platform : {platform.system()} {platform.release()} ({platform.machine()})"))
    print(DIM(f"  Python   : {sys.version.split()[0]}"))
    print(DIM(f"  CWD      : {Path.cwd()}"))
    print(BOLD("─" * width))
    print()

    for r in report.results:
        badge  = _status_badge(r.status)
        detail = DIM(f"  {r.detail}") if r.detail else ""
        print(f"  {badge}  {r.name}{detail}")
        if r.fix and r.status in (Status.FAIL, Status.WARN):
            for line in r.fix.splitlines():
                print(f"            {DIM('→')} {line}")
    print()

    ok, warn, fail, skip = report.summary
    print(BOLD("─" * width))
    summary_parts = [
        GREEN(f"{ok} OK"),
        YELLOW(f"{warn} WARN") if warn else DIM("0 WARN"),
        RED(f"{fail} FAIL") if fail else DIM("0 FAIL"),
        DIM(f"{skip} SKIP"),
    ]
    print("  " + "   ".join(summary_parts))

    if report.has_failures:
        print()
        print(RED("  ✗ Critical checks failed. Fix the items above before proceeding."))
        print(RED("    Run  make setup  to install missing dependencies."))
    else:
        print()
        print(GREEN("  ✓ All critical checks passed."))
        print(DIM("    Run  make collect  to start data collection (requires CARLA)."))
    print(BOLD("─" * width))
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CARLA Foundation Driving Demo — environment diagnostics"
    )
    parser.add_argument("--config", default="config/default.yaml", help="Config file path")
    parser.add_argument("--profile", default="local_dev", help="Config profile name")
    parser.add_argument("--carla-host", default="localhost", help="CARLA server host")
    parser.add_argument("--carla-port", type=int, default=2000, help="CARLA server port")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any WARN too")
    args = parser.parse_args()

    report = DiagnosticsReport()

    # ── Core environment ───────────────────────────────────────────────────────
    report.add(check_python_version())
    report.add(check_git())
    report.add(check_uv())

    # ── Required Python packages ───────────────────────────────────────────────
    report.add(check_package("yaml", "pyyaml", install_hint="uv pip install pyyaml"))
    report.add(check_package("structlog", critical=True, install_hint="uv pip install structlog"))
    report.add(check_package("numpy", critical=True, install_hint="uv pip install numpy"))
    report.add(check_package("rich", critical=False, install_hint="uv pip install rich"))
    report.add(check_package("click", critical=False, install_hint="uv pip install click"))
    report.add(check_package("tqdm", critical=False, install_hint="uv pip install tqdm"))

    # ── Simulation packages (optional) ────────────────────────────────────────
    report.add(check_package(
        "cv2", "opencv-python", critical=False,
        install_hint="uv pip install opencv-python",
    ))
    report.add(check_package(
        "PIL", "Pillow", critical=False,
        install_hint="uv pip install Pillow",
    ))
    report.add(check_package(
        "h5py", critical=False,
        install_hint="uv pip install h5py",
    ))
    report.add(check_carla())

    # ── ML packages (optional) ────────────────────────────────────────────────
    report.add(check_package(
        "torch", "PyTorch", critical=False,
        install_hint="uv pip install torch torchvision",
    ))
    report.add(check_package(
        "tensorboard", critical=False,
        install_hint="uv pip install tensorboard",
    ))

    # ── Dev tools ─────────────────────────────────────────────────────────────
    report.add(check_package(
        "ruff", critical=False,
        install_hint="uv pip install ruff",
    ))
    report.add(check_package(
        "mypy", critical=False,
        install_hint="uv pip install mypy",
    ))
    report.add(check_package(
        "pytest", critical=False,
        install_hint="uv pip install pytest",
    ))

    # ── GPU ────────────────────────────────────────────────────────────────────
    report.add(check_cuda())

    # ── CARLA server connectivity ─────────────────────────────────────────────
    report.add(check_carla_server(host=args.carla_host, port=args.carla_port))

    # ── Data directories ──────────────────────────────────────────────────────
    for r in check_data_dirs():
        report.add(r)

    # ── Environment variables ─────────────────────────────────────────────────
    for r in check_env_vars():
        report.add(r)

    # ── Output ────────────────────────────────────────────────────────────────
    print_report(report)

    if report.has_failures:
        sys.exit(1)

    if args.strict:
        ok, warn, fail, skip = report.summary
        if warn > 0:
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
