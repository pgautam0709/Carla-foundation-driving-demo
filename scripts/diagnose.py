#!/usr/bin/env python3
"""
scripts/diagnose.py — Dependency health check and environment diagnostics.

Self-contained: no project imports required. Can be run before ``make setup``.

Usage::

    python scripts/diagnose.py
    python scripts/diagnose.py --profile macos_docker
    python scripts/diagnose.py --carla-host 192.168.1.5 --carla-port 2000
    python scripts/diagnose.py --strict   # exit 1 on any WARN too

Exit codes:
    0  All critical checks passed (WARNs are acceptable)
    1  One or more critical checks FAILED

Environment variables honoured:
    CARLA_HOST            Override CARLA server host
    CARLA_PORT            Override CARLA server port
    CARLA_DOCKER_IMAGE    Override Docker image name
    CARLA_PYTHON_API_PATH Path to CARLA wheel dist directory
"""

from __future__ import annotations

import argparse
import contextlib
import copy
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
        """Append a single check result."""
        self.results.append(result)

    @property
    def has_failures(self) -> bool:
        """Return True if any critical check failed."""
        return any(r.status == Status.FAIL and r.critical for r in self.results)

    @property
    def summary(self) -> tuple[int, int, int, int]:
        """Return (ok, warn, fail, skip) counts."""
        ok   = sum(1 for r in self.results if r.status == Status.OK)
        warn = sum(1 for r in self.results if r.status == Status.WARN)
        fail = sum(1 for r in self.results if r.status == Status.FAIL)
        skip = sum(1 for r in self.results if r.status == Status.SKIP)
        return ok, warn, fail, skip


# ── Config loader (self-contained, no src/ imports) ────────────────────────────

def _simple_deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _simple_deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def _load_cfg(config_path: str, profile: str) -> dict:
    """Load YAML config + profile for diagnose. Silent on missing yaml."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return {}
    try:
        p = Path(config_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[1] / p
        if not p.exists():
            return {}
        with p.open() as f:
            cfg: dict = yaml.safe_load(f) or {}

        if profile:
            pp = p.parent / "profiles" / f"{profile}.yaml"
            if pp.exists():
                with pp.open() as f:
                    override: dict = yaml.safe_load(f) or {}
                cfg = _simple_deep_merge(cfg, override)

        # Apply env var overrides into carla_connection section
        conn = cfg.setdefault("carla_connection", {})
        if h := os.environ.get("CARLA_HOST"):
            conn["host"] = h
        if ps := os.environ.get("CARLA_PORT"):
            with contextlib.suppress(ValueError):
                conn["port"] = int(ps)
        if v := os.environ.get("CARLA_VERSION"):
            conn["version"] = v
        if ap := os.environ.get("CARLA_PYTHON_API_PATH"):
            conn["python_api_path"] = ap

        return cfg
    except Exception:
        return {}


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


# ── Docker checks ──────────────────────────────────────────────────────────────

def check_docker_installed() -> CheckResult:
    """Check that the Docker CLI is available."""
    docker = shutil.which("docker")
    if docker is None:
        return CheckResult(
            "Docker CLI", Status.WARN,
            "Not found in PATH",
            "Install Docker Desktop: https://docs.docker.com/get-docker/",
            critical=False,
        )
    result = subprocess.run(["docker", "--version"], capture_output=True, text=True)
    return CheckResult("Docker CLI", Status.OK, result.stdout.strip(), critical=False)


def check_docker_daemon() -> CheckResult:
    """Check that the Docker daemon is running."""
    if shutil.which("docker") is None:
        return CheckResult(
            "Docker daemon", Status.SKIP,
            "Docker CLI not installed — skipping",
            critical=False,
        )
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return CheckResult("Docker daemon", Status.OK, "Running", critical=False)
        return CheckResult(
            "Docker daemon", Status.WARN,
            "Not running or permission denied",
            "Start Docker Desktop  or  sudo systemctl start docker",
            critical=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return CheckResult(
            "Docker daemon", Status.WARN,
            "Could not query daemon (timeout)",
            critical=False,
        )


def check_docker_image(image: str) -> CheckResult:
    """Check whether a Docker image has been pulled locally."""
    if shutil.which("docker") is None:
        return CheckResult(
            f"Docker image: {image}", Status.SKIP,
            "Docker CLI not installed",
            critical=False,
        )
    try:
        result = subprocess.run(
            ["docker", "images", "-q", image],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode == 0 and result.stdout.strip():
            return CheckResult(
                f"Docker image: {image}", Status.OK,
                "Pulled locally",
                critical=False,
            )
        return CheckResult(
            f"Docker image: {image}", Status.WARN,
            "Not pulled locally",
            f"docker pull {image}",
            critical=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return CheckResult(
            f"Docker image: {image}", Status.SKIP,
            "Could not query local Docker images",
            critical=False,
        )


# ── Package checks ─────────────────────────────────────────────────────────────

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
        try:
            from packaging.version import Version  # type: ignore[import]
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


def check_carla_package(api_path: str | None = None) -> CheckResult:
    """Check the CARLA Python package installation."""
    spec = importlib.util.find_spec("carla")
    if spec is None:
        fix_lines = (
            "Download CARLA 0.9.15 from https://github.com/carla-simulator/carla/releases"
        )
        if api_path:
            fix_lines += f"\n    Then: pip install {api_path}/carla-0.9.15-cp310-*.whl"
        else:
            fix_lines += (
                "\n    Then: pip install <CARLA_ROOT>/PythonAPI/carla/dist/carla-0.9.15-cp310-*.whl"
                "\n    Or set CARLA_PYTHON_API_PATH to the dist directory"
            )
        return CheckResult(
            "carla Python package", Status.WARN,
            "Not installed — simulation requires CARLA wheel",
            fix_lines,
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


def check_carla_api_path(api_path: str | None) -> CheckResult:
    """Check the CARLA Python API path env var / config value."""
    if not api_path:
        return CheckResult(
            "CARLA_PYTHON_API_PATH", Status.SKIP,
            "Not set — optional if carla package already installed",
            "export CARLA_PYTHON_API_PATH=<CARLA_ROOT>/PythonAPI/carla/dist/",
            critical=False,
        )
    path = Path(api_path)
    if not path.exists():
        return CheckResult(
            "CARLA_PYTHON_API_PATH", Status.WARN,
            f"Path not found: {path}",
            "Set CARLA_PYTHON_API_PATH to <CARLA_ROOT>/PythonAPI/carla/dist/",
            critical=False,
        )
    wheels = list(path.glob("*.whl"))
    if wheels:
        return CheckResult(
            "CARLA_PYTHON_API_PATH", Status.OK,
            f"{path}  ({len(wheels)} wheel(s))",
        )
    return CheckResult(
        "CARLA_PYTHON_API_PATH", Status.WARN,
        f"Path exists but no .whl files: {path}",
        "Verify CARLA_ROOT points to the CARLA installation directory",
        critical=False,
    )


def check_runtime_mode(mode: str, image: str) -> CheckResult:
    """Report the active runtime mode (docker / local / remote)."""
    detail_map = {
        "docker": f"docker  ({image})",
        "local":  "local native CARLA",
        "remote": "remote CARLA server",
    }
    detail = detail_map.get(mode, mode)
    return CheckResult("CARLA runtime mode", Status.OK, detail, critical=False)


def check_carla_server(
    host: str = "localhost", port: int = 2000, timeout: float = 3.0
) -> CheckResult:
    """Check TCP connectivity to the CARLA server.

    Note: This is always WARN (never FAIL) because the CARLA server is
    an optional external service — not having it does not prevent
    running make lint, make test, or make diagnose on macOS.
    """
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
            "Start CARLA: make carla-docker  or  ./CarlaUE4.sh -RenderOffScreen",
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


def check_env_vars(cfg: dict) -> list[CheckResult]:
    """Check relevant environment variables."""
    results = []
    conn = cfg.get("carla_connection", {})
    rt = cfg.get("runtime", {})

    # CARLA_HOST / CARLA_PORT — show effective values
    host = os.environ.get("CARLA_HOST") or conn.get("host", "localhost")
    port = os.environ.get("CARLA_PORT") or str(conn.get("port", 2000))
    src_h = "env" if os.environ.get("CARLA_HOST") else "config"
    src_p = "env" if os.environ.get("CARLA_PORT") else "config"
    results.append(CheckResult(
        "CARLA_HOST", Status.OK,
        f"{host}  (source: {src_h})",
        critical=False,
    ))
    results.append(CheckResult(
        "CARLA_PORT", Status.OK,
        f"{port}  (source: {src_p})",
        critical=False,
    ))

    # CARLA_DOCKER_IMAGE
    docker_img = os.environ.get("CARLA_DOCKER_IMAGE") or rt.get(
        "docker_image", "carlasim/carla:0.9.15"
    )
    img_src = "env" if os.environ.get("CARLA_DOCKER_IMAGE") else "config"
    results.append(CheckResult(
        "CARLA_DOCKER_IMAGE", Status.OK,
        f"{docker_img}  (source: {img_src})",
        critical=False,
    ))

    # CUDA_VISIBLE_DEVICES — optional
    cuda_dev = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cuda_dev:
        results.append(CheckResult(
            "CUDA_VISIBLE_DEVICES", Status.OK, cuda_dev, critical=False
        ))
    else:
        results.append(CheckResult(
            "CUDA_VISIBLE_DEVICES", Status.SKIP,
            "Not set — all GPUs visible (optional)",
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


def print_report(
    report: DiagnosticsReport,
    carla_host: str,
    carla_port: int,
    runtime_mode: str,
) -> None:
    width = 72
    print()
    print(BOLD("─" * width))
    print(BOLD(CYAN("  CARLA Foundation Driving Demo — Environment Diagnostics")))
    print(BOLD("─" * width))
    print(DIM(f"  Platform : {platform.system()} {platform.release()} ({platform.machine()})"))
    print(DIM(f"  Python   : {sys.version.split()[0]}"))
    print(DIM(f"  CWD      : {Path.cwd()}"))
    print(DIM(f"  Runtime  : {runtime_mode}"))
    print(DIM(f"  CARLA    : {carla_host}:{carla_port}"))
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
        print(DIM("    Run  make smoke  to test CARLA connectivity (requires CARLA server)."))
    print(BOLD("─" * width))
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CARLA Foundation Driving Demo — environment diagnostics"
    )
    parser.add_argument("--config", default="config/default.yaml",
                        help="Config file path")
    parser.add_argument("--profile", default="local_dev",
                        help="Config profile name")
    parser.add_argument("--carla-host", default=None,
                        help="Override CARLA server host (also: CARLA_HOST env var)")
    parser.add_argument("--carla-port", type=int, default=None,
                        help="Override CARLA server port (also: CARLA_PORT env var)")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 on any WARN too")
    args = parser.parse_args()

    # Load config (includes env var overrides)
    cfg = _load_cfg(args.config, args.profile)
    conn = cfg.get("carla_connection", {})
    rt   = cfg.get("runtime", {})

    # Resolve CARLA host/port: CLI arg > env var > config > hardcoded default
    carla_host = (
        args.carla_host
        or os.environ.get("CARLA_HOST")
        or conn.get("host", "localhost")
    )
    carla_port = (
        args.carla_port
        or (int(os.environ.get("CARLA_PORT")) if os.environ.get("CARLA_PORT") else None)
        or conn.get("port", 2000)
    )
    runtime_mode  = rt.get("mode", "local")
    docker_image  = (
        os.environ.get("CARLA_DOCKER_IMAGE")
        or rt.get("docker_image", "carlasim/carla:0.9.15")
    )
    api_path: str | None = (
        os.environ.get("CARLA_PYTHON_API_PATH")
        or conn.get("python_api_path")
        or None
    )

    report = DiagnosticsReport()

    # ── Core environment ───────────────────────────────────────────────────────
    report.add(check_python_version())
    report.add(check_git())
    report.add(check_uv())

    # ── OS / runtime context ──────────────────────────────────────────────────
    report.add(check_runtime_mode(runtime_mode, docker_image))

    # ── Docker ────────────────────────────────────────────────────────────────
    report.add(check_docker_installed())
    report.add(check_docker_daemon())
    if runtime_mode == "docker":
        report.add(check_docker_image(docker_image))

    # ── Required Python packages ───────────────────────────────────────────────
    report.add(check_package("yaml", "pyyaml", install_hint="uv pip install pyyaml"))
    report.add(check_package("structlog", critical=True,
                             install_hint="uv pip install structlog"))
    report.add(check_package("numpy", critical=True,
                             install_hint="uv pip install numpy"))
    report.add(check_package("rich", critical=False,
                             install_hint="uv pip install rich"))
    report.add(check_package("click", critical=False,
                             install_hint="uv pip install click"))
    report.add(check_package("tqdm", critical=False,
                             install_hint="uv pip install tqdm"))

    # ── Simulation packages ────────────────────────────────────────────────────
    report.add(check_package("cv2", "opencv-python", critical=False,
                             install_hint="uv pip install opencv-python"))
    report.add(check_package("PIL", "Pillow", critical=False,
                             install_hint="uv pip install Pillow"))
    report.add(check_package("h5py", critical=False,
                             install_hint="uv pip install h5py"))
    report.add(check_carla_api_path(api_path))
    report.add(check_carla_package(api_path))

    # ── ML packages ───────────────────────────────────────────────────────────
    report.add(check_package("torch", "PyTorch", critical=False,
                             install_hint="uv pip install torch torchvision"))
    report.add(check_package("tensorboard", critical=False,
                             install_hint="uv pip install tensorboard"))

    # ── Dev tools ─────────────────────────────────────────────────────────────
    report.add(check_package("ruff", critical=False,
                             install_hint="uv pip install ruff"))
    report.add(check_package("mypy", critical=False,
                             install_hint="uv pip install mypy"))
    report.add(check_package("pytest", critical=False,
                             install_hint="uv pip install pytest"))

    # ── GPU ────────────────────────────────────────────────────────────────────
    report.add(check_cuda())

    # ── CARLA server connectivity ─────────────────────────────────────────────
    report.add(check_carla_server(host=carla_host, port=carla_port))

    # ── Data directories ──────────────────────────────────────────────────────
    for r in check_data_dirs():
        report.add(r)

    # ── Environment variables ─────────────────────────────────────────────────
    for r in check_env_vars(cfg):
        report.add(r)

    # ── Output ────────────────────────────────────────────────────────────────
    print_report(report, carla_host, carla_port, runtime_mode)

    if report.has_failures:
        sys.exit(1)

    if args.strict:
        _, warn, _fail, _skip = report.summary
        if warn > 0:
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
