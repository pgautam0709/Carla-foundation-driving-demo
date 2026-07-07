.DEFAULT_GOAL := help

# ── Variables ──────────────────────────────────────────────────────────────────
PYTHON      := .venv/bin/python
UV          := uv
RUFF        := .venv/bin/ruff
MYPY        := .venv/bin/mypy
PYTEST      := .venv/bin/pytest
CONFIG      ?= config/default.yaml
PROFILE     ?= local_dev

# CARLA connection — override with env vars or on the command line:
#   CARLA_HOST=192.168.1.5 make smoke
#   CARLA_PORT=3000 make diagnose
CARLA_HOST  ?= localhost
CARLA_PORT  ?= 2000

# ── Phony targets ──────────────────────────────────────────────────────────────
.PHONY: help setup diagnose lint lint-fix type-check test test-all \
        smoke carla-docker carla-windows-help \
        collect train clean

help: ## Show this help message
	@echo ""
	@echo "  CARLA Foundation Driving Demo — Developer Commands"
	@echo "  ──────────────────────────────────────────────────"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  CARLA connection (override with env vars):"
	@echo "    CARLA_HOST=$(CARLA_HOST)  CARLA_PORT=$(CARLA_PORT)"
	@echo ""

setup: ## Bootstrap the development environment using uv
	@echo "→ Checking for uv..."
	@command -v $(UV) >/dev/null 2>&1 || (echo "uv not found. Install: curl -Ls https://astral.sh/uv/install.sh | sh" && exit 1)
	@echo "→ Creating virtual environment with Python 3.10..."
	$(UV) venv --python 3.10 .venv
	@echo "→ Installing core + dev dependencies..."
	$(UV) pip install -e ".[dev]"
	@echo "→ Installing sim dependencies (skip if no CARLA)..."
	$(UV) pip install -e ".[sim]" || echo "  [WARN] Sim deps skipped — install CARLA wheel manually."
	@echo ""
	@echo "  Done. Activate: source .venv/bin/activate"
	@echo "  Run diagnostics: make diagnose"

diagnose: ## Run dependency health check and environment diagnostics
	$(PYTHON) scripts/diagnose.py \
		--config $(CONFIG) \
		--profile $(PROFILE) \
		--carla-host $(CARLA_HOST) \
		--carla-port $(CARLA_PORT)

lint: ## Run ruff linter
	$(RUFF) check src/ scripts/ tests/

lint-fix: ## Auto-fix ruff lint issues
	$(RUFF) check --fix src/ scripts/ tests/

type-check: ## Run mypy static type checker
	$(MYPY) src/ scripts/

test: ## Run unit tests only (no CARLA required)
	$(PYTEST) tests/unit/ -m "not integration"

test-all: ## Run all tests including integration (requires CARLA server)
	$(PYTEST) tests/

# ── CARLA runtime targets ──────────────────────────────────────────────────────

smoke: ## Run Phase 1 smoke test (requires CARLA — set CARLA_HOST/PORT)
	$(PYTHON) scripts/smoke_test.py \
		--profile $(PROFILE) \
		--host $(CARLA_HOST) \
		--port $(CARLA_PORT)

carla-docker: ## Start CARLA server via Docker (macOS/Linux)
	@bash scripts/start_carla_docker.sh

carla-windows-help: ## Print Windows CARLA startup instructions
	@echo ""
	@echo "  Windows CARLA Startup"
	@echo "  ────────────────────────────────────────────────────────────────"
	@echo ""
	@echo "  1. Download CARLA 0.9.15 for Windows from:"
	@echo "       https://github.com/carla-simulator/carla/releases/tag/0.9.15"
	@echo ""
	@echo "  2. Start CARLA (run in PowerShell):"
	@echo ""
	@echo "       powershell -ExecutionPolicy Bypass \\"
	@echo "           -File scripts\\start_carla_windows.ps1 \\"
	@echo "           -CARLA_ROOT C:\\CARLA\\CARLA_0.9.15"
	@echo ""
	@echo "  3. Install the Python wheel:"
	@echo ""
	@echo "       pip install C:\\CARLA\\CARLA_0.9.15\\PythonAPI\\carla\\dist\\carla-0.9.15-cp310-*.whl"
	@echo ""
	@echo "  4. Run the smoke test:"
	@echo ""
	@echo "       set CARLA_HOST=127.0.0.1"
	@echo "       set PROFILE=windows_local"
	@echo "       make smoke"
	@echo ""
	@echo "  See docs/PHASE1_SMOKE_TEST.md for the full guide."
	@echo ""

# ── Data pipeline ─────────────────────────────────────────────────────────────

collect: ## Run data collection (requires CARLA server)
	$(PYTHON) scripts/collect_data.py --config $(CONFIG) --profile $(PROFILE)

train: ## Run model training (requires collected data)
	$(PYTHON) scripts/train.py --config $(CONFIG) --profile $(PROFILE)

# ── Maintenance ───────────────────────────────────────────────────────────────

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build
	@echo "Clean."
