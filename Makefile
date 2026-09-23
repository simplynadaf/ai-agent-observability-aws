# AI Agent Observability on AWS — one-command demo targets.
# .env is auto-loaded by the code (src/crew.py), so you do NOT need to `source .env`.
# Just: cp .env.example .env, paste your Traccia key (optional), then `make run`.

# Auto-detect a Python 3 interpreter (python3 on most systems; python on some). Override:
#   make PY=python3.11 setup
PY ?= $(shell command -v python3 || command -v python)
VENV ?= .venv
VPY := $(VENV)/bin/python

.DEFAULT_GOAL := help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup:  ## Create venv + install pinned deps + git hooks; copies .env.example -> .env
	$(PY) -m venv $(VENV)
	$(VPY) -m pip install -U pip
	$(VPY) -m pip install -r requirements.txt
	@[ -f .env ] || cp .env.example .env
	@[ -f scripts/install-hooks.sh ] && bash scripts/install-hooks.sh || true
	@echo "Setup done. Put your TRACCIA_API_KEY in .env (optional — the demo runs \$$0 local without it)."

run:  ## Run the crew against real AWS -> writes traces.jsonl (needs AWS creds)
	$(VPY) -m src.crew

verify:  ## Render the nested trace tree + per-agent cost table (run `make run` first)
	@[ -f traces.jsonl ] || { echo "No traces.jsonl yet. Run 'make run' (or 'make waste') first to generate it."; exit 1; }
	$(VPY) -m src.view_trace

waste:  ## Clean baseline + 3 silent-waste scenarios + delta verdict
	$(VPY) -m src.waste_demo

compare:  ## Side-by-side "same answer, different bill" (run `make waste` first)
	@[ -f traces_clean.jsonl ] && [ -f traces_bloat.jsonl ] || { echo "Missing traces_clean.jsonl / traces_bloat.jsonl. Run 'make waste' first to generate them."; exit 1; }
	$(VPY) -m src.compare traces_clean.jsonl traces_bloat.jsonl

clean:  ## Remove trace output + caches
	rm -f traces*.jsonl
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

.PHONY: help setup run verify waste compare clean
