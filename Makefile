.DEFAULT_GOAL := help

# ── Variables ──────────────────────────────────────────────────────────────────
PYTHON      := python3
UV          := uv
RUFF        := ruff
MYPY        := mypy
PYTEST      := pytest
CONFIG      ?= config/default.yaml
PROFILE     ?= local_dev

# ── Phony targets ──────────────────────────────────────────────────────────────
.PHONY: help setup diagnose lint type-check test test-all collect train clean

help: ## Show this help message
	@echo ""
	@echo "  CARLA Foundation Driving Demo — Developer Commands"
	@echo "  ──────────────────────────────────────────────────"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
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
	$(PYTHON) scripts/diagnose.py --config $(CONFIG) --profile $(PROFILE)

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

collect: ## Run data collection (requires CARLA server)
	$(PYTHON) scripts/collect_data.py --config $(CONFIG) --profile $(PROFILE)

train: ## Run model training (requires collected data)
	$(PYTHON) scripts/train.py --config $(CONFIG) --profile $(PROFILE)

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build
	@echo "Clean."
