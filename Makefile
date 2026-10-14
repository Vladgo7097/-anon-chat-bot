# Local developer entry points. The deployed containers are built by the
# post-receive hook on the server; nothing here touches the deploy path.
#
# Everything runs out of .venv so a fresh clone reproduces the working
# environment (475 tests) instead of quietly depending on whatever the host
# Python happens to have -- which is how five discord tests were being
# silently skipped before discord.py was declared in requirements-dev.txt.

PYTHON := .venv/bin/python
RUFF   := $(HOME)/.local/bin/ruff
PYTEST := $(PYTHON) -m pytest tests/ -q --ignore=tests/test_postgres.py

.PHONY: venv install test cov lint fmt check clean

## venv: create the environment from the pinned requirements
venv: .venv/bin/python

.venv/bin/python: requirements.txt requirements-dev.txt
	python3 -m venv .venv
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt
	@touch $@

install: venv

## test: the suite the deploy gate runs
test:
	$(PYTEST)

## cov: same suite with coverage (currently 82% of 12270 statements)
cov:
	$(PYTEST) --cov=. --cov-report=term:skip-covered

## lint: defect rules only -- see ruff.toml for why not the defaults
lint:
	$(RUFF) check .

## fmt: reformat the tree (107 files today); review it as its own commit
fmt:
	$(RUFF) format .

## check: what a strict pre-commit would enforce once the tree is formatted
check: lint
	$(RUFF) format --check .

## clean: build artifacts only, never the venv or working tree
clean:
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov
