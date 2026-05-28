# =============================================================================
# Agent 1 — P2P Exception Orchestrator — common commands
# =============================================================================
#
# All targets are .PHONY because they don't produce files of their own name.
# Run `make help` for the catalog.

.PHONY: help setup install sync test test-unit test-golden test-integration \
        test-cov lint format typecheck check corpus corpus-bpi corpus-synthetic \
        corpus-prompts-invoices corpus-ingest-invoices corpus-prompts-emails \
        corpus-ingest-emails corpus-api-invoices corpus-api-invoices-validate \
        corpus-api corpus-api-validate sap-validate \
        classifier-eval classifier-eval-full extractor-eval extractor-eval-full \
        pipeline-eval pipeline-eval-full \
        hitl-serve hitl-seed hitl-clear demo-warmup \
        clean clean-corpus clean-logs stage9 estimate-cost run-case \
        db-up db-down db-reset

# Default target — show help.
help:
	@echo "Agent 1 — P2P Exception Orchestrator"
	@echo ""
	@echo "Setup:"
	@echo "  setup            Install uv + sync dependencies"
	@echo "  sync             Sync dependencies (after editing pyproject.toml)"
	@echo ""
	@echo "Tests:"
	@echo "  test             Run all tests (unit + golden, skip integration)"
	@echo "  test-unit        Run unit tests only"
	@echo "  test-golden      Run the golden-cases regression set"
	@echo "  test-integration Run integration tests (requires ERP sandbox env)"
	@echo "  test-cov         Run unit tests with coverage"
	@echo ""
	@echo "Quality:"
	@echo "  lint             Run ruff lint"
	@echo "  format           Run ruff format"
	@echo "  typecheck        Run pyright"
	@echo "  check            lint + typecheck (CI entry point)"
	@echo ""
	@echo "Test corpus:"
	@echo "  corpus                       Build the full test corpus (BPI + synthetic)"
	@echo "  corpus-bpi                   Download + ingest BPI Challenge datasets"
	@echo "  corpus-prompts-invoices      [subscription] Generate invoice-batch prompts for Claude.ai/ChatGPT"
	@echo "  corpus-ingest-invoices       [subscription] Ingest pasted chat responses into the corpus"
	@echo "  corpus-prompts-emails        [subscription] (Coming) Generate email-thread prompts"
	@echo "  corpus-ingest-emails         [subscription] (Coming) Ingest email responses"
	@echo "  corpus-synthetic             [subscription] Run prompts + ingest end to end"
	@echo "  corpus-api-invoices-validate [api]    Generate 25 invoices via OpenRouter (validation pass)"
	@echo "  corpus-api-invoices          [api]    Generate 500 invoices via OpenRouter"
	@echo "  corpus-api                   [api]    Full corpus: generate via API + render PDFs"
	@echo ""
	@echo "Stage 9:"
	@echo "  stage9           Compute Stage 9 metrics from the latest run"
	@echo "  estimate-cost    Estimate cost of running the full golden set"
	@echo ""
	@echo "Run / debug:"
	@echo "  run-case CASE=GTC-002   Run one golden case end-to-end"
	@echo ""
	@echo "ERP sandbox:"
	@echo "  sap-validate     Validate SAP S/4HANA trial connection"
	@echo ""
	@echo "Classifier eval (corpus-driven):"
	@echo "  classifier-eval       Run classifier against a 100-invoice random sample of the corpus"
	@echo "  classifier-eval-full  Run classifier against all corpus invoices"
	@echo ""
	@echo "Extractor eval (corpus-driven):"
	@echo "  extractor-eval        Run extractor against 100 sampled PDFs; diff vs ground-truth"
	@echo "  extractor-eval-full   Run extractor against all 490 PDFs"
	@echo ""
	@echo "Pipeline eval (end-to-end PDF → Classification):"
	@echo "  pipeline-eval         Run pipeline against 100 sampled invoices"
	@echo "  pipeline-eval-full    Run pipeline against all 490 invoices"
	@echo ""
	@echo "HITL approval queue (demo UI):"
	@echo "  hitl-serve            Start FastAPI demo at http://localhost:8080/queue"
	@echo "  hitl-seed             Populate queue with 10 sampled invoices"
	@echo "  demo-warmup           Pre-seed LLM prompt cache (run 3 min before a demo)"
	@echo "  hitl-clear            Wipe the queue DB"
	@echo ""
	@echo "Cleanup:"
	@echo "  clean            Remove Python caches and build artifacts"
	@echo "  clean-corpus     Remove generated test corpus (keeps BPI raw data)"
	@echo "  clean-logs       Remove llm_calls.jsonl and stage9 traces"
	@echo ""
	@echo "Database:"
	@echo "  db-up            Start Postgres + pgvector via Docker"
	@echo "  db-down          Stop Postgres"
	@echo "  db-reset         Drop + recreate the agent database"

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------

setup:
	@command -v uv >/dev/null 2>&1 || { echo "Installing uv..."; curl -LsSf https://astral.sh/uv/install.sh | sh; }
	uv sync --all-extras
	@echo "Setup complete. Copy .env.example to .env and fill in API keys."

sync:
	uv sync --all-extras

install: sync

# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

test:
	uv run pytest -m "not integration"

test-unit:
	uv run pytest -m "not golden and not integration"

test-golden:
	uv run pytest -m golden -v

test-integration:
	uv run pytest -m integration -v

test-cov:
	uv run pytest -m "not integration" --cov=src/p2p_agent --cov-report=term-missing --cov-report=html

# -----------------------------------------------------------------------------
# Quality
# -----------------------------------------------------------------------------

lint:
	uv run ruff check src tests scripts

format:
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts

typecheck:
	uv run pyright src tests scripts

check: lint typecheck

# -----------------------------------------------------------------------------
# Test corpus
# -----------------------------------------------------------------------------

corpus: corpus-bpi corpus-prompts-invoices
	@echo ""
	@echo "Prompts are ready. Process them in Claude.ai or ChatGPT — see"
	@echo "docs/subscription_mode_workflow.md for the workflow. When responses"
	@echo "are pasted into scripts/subscription_workflow/responses/, run"
	@echo "'make corpus-ingest-invoices' to ingest them into the corpus."

corpus-bpi:
	uv run python scripts/ingest_bpi.py

corpus-prompts-invoices:
	uv run python scripts/generate_invoice_prompts.py --count 500 --batch-size 5

# On macOS, weasyprint needs to find Homebrew's pango/cairo/glib dylibs.
# Setting DYLD_FALLBACK_LIBRARY_PATH at the recipe level is the reliable
# fix (env vars set inside Python after launch don't reach dyld).
corpus-ingest-invoices:
	DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$$DYLD_FALLBACK_LIBRARY_PATH \
	uv run python scripts/ingest_subscription_responses.py --asset invoices

corpus-prompts-emails:
	@echo "(Coming) Email-thread prompt generator — scripts/generate_email_prompts.py"

corpus-ingest-emails:
	uv run python scripts/ingest_subscription_responses.py --asset emails

corpus-synthetic: corpus-prompts-invoices
	@echo "Now paste prompts into Claude.ai/ChatGPT, save responses, then run corpus-ingest-invoices."

# API mode — uses OpenRouter via src/p2p_agent/llm/client.py
corpus-api-invoices-validate:
	uv run python scripts/generate_invoices.py --count 25 --batch-size 5

corpus-api-invoices:
	uv run python scripts/generate_invoices.py --count 500 --batch-size 5

corpus-api: corpus-api-invoices corpus-ingest-invoices
	@echo ""
	@echo "Corpus generated end-to-end. Inspect:"
	@echo "  test_corpus/synthetic/invoices/*.pdf"
	@echo "  logs/llm_calls.jsonl   (cost ledger)"

corpus-api-validate: corpus-api-invoices-validate corpus-ingest-invoices
	@echo ""
	@echo "Validation pass complete (25 invoices). Eyeball the PDFs before scaling."

# -----------------------------------------------------------------------------
# Stage 9
# -----------------------------------------------------------------------------

stage9:
	uv run python scripts/compute_stage9.py

estimate-cost:
	uv run python scripts/estimate_cost.py --suite golden

# -----------------------------------------------------------------------------
# Run / debug
# -----------------------------------------------------------------------------

CASE ?= GTC-001
run-case:
	uv run python scripts/run_golden_set.py --case $(CASE)

# -----------------------------------------------------------------------------
# ERP sandbox
# -----------------------------------------------------------------------------

sap-validate:
	uv run python scripts/validate_sap_connection.py

# -----------------------------------------------------------------------------
# Classifier corpus eval
# -----------------------------------------------------------------------------

classifier-eval:
	uv run python scripts/eval_classifier.py --sample 100

classifier-eval-full:
	uv run python scripts/eval_classifier.py --full

# -----------------------------------------------------------------------------
# Extractor corpus eval (PDFs → InvoiceExtraction, diffed vs ground-truth JSON)
# -----------------------------------------------------------------------------

extractor-eval:
	uv run python scripts/eval_extractor.py --sample 100

extractor-eval-full:
	uv run python scripts/eval_extractor.py --full

# -----------------------------------------------------------------------------
# Pipeline eval (end-to-end PDF → Classification, M1; later: + Recommendation)
# -----------------------------------------------------------------------------

pipeline-eval:
	uv run python scripts/eval_pipeline.py --sample 100

pipeline-eval-full:
	uv run python scripts/eval_pipeline.py --full

# -----------------------------------------------------------------------------
# HITL approval queue (SQLite + FastAPI demo UI)
# -----------------------------------------------------------------------------
# `hitl-serve` uses --reload (dev only). Production would drop --reload and use
# gunicorn / uvicorn workers behind a reverse proxy.

hitl-serve:
	uv run uvicorn p2p_agent.hitl.webapp.server:app --host 127.0.0.1 --port 8080 --reload

# Production-style boot: binds 0.0.0.0 and reads $$PORT from environment.
# Railway uses this (via Procfile) — local invocation here too if you want
# to test the prod start command before deploying.
hitl-serve-prod:
	uv run uvicorn p2p_agent.hitl.webapp.server:app --host 0.0.0.0 --port $${PORT:-8080}

hitl-seed:
	uv run python scripts/seed_hitl_queue.py --n 10

# Pre-warm the LLM prompt cache + verify live latency before a demo.
# Run ~3 minutes before the meeting. Loads bge-large-en, seeds DeepSeek's
# prompt cache for extract/classify/decide/draft, prints per-step latency
# so you know if today is a slow OpenRouter day.
demo-warmup:
	uv run python scripts/demo_warmup.py

hitl-clear:
	uv run python -c "from p2p_agent.hitl import HITLQueue; HITLQueue().clear(); print('Queue cleared.')"

# -----------------------------------------------------------------------------
# Cleanup
# -----------------------------------------------------------------------------

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .pyright -exec rm -rf {} +
	rm -rf build/ dist/ *.egg-info htmlcov/ .coverage

clean-corpus:
	rm -rf test_corpus/synthetic/invoices/*
	rm -rf test_corpus/synthetic/emails/*
	rm -rf test_corpus/synthetic/master_data/*
	@echo "(BPI raw data kept in test_corpus/bpi_data/)"

clean-logs:
	rm -rf logs/*
	rm -rf stage9_traces/*

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

db-reset:
	docker compose down -v
	docker compose up -d postgres
	@echo "Waiting for Postgres..."
	@sleep 3
	uv run python scripts/init_db.py
