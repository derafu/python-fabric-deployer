.PHONY: install-dev lint typecheck check clean

# Python used to create the environment (3.14): `make PYTHON=python3.14`.
PYTHON ?= python3

# Named "venv" because [tool.pyright] looks for the environment there.
VENV = venv
VENV_READY = $(VENV)/.installed

# Created on the first command and reinstalled when pyproject.toml changes.
# An existing environment is not created again, so its Python is kept.
$(VENV_READY): pyproject.toml
	test -x $(VENV)/bin/python || $(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e .
	$(VENV)/bin/python -m pip install --group dev
	touch $(VENV_READY)

install-dev: $(VENV_READY)

lint: $(VENV_READY)
	$(VENV)/bin/ruff check .

typecheck: $(VENV_READY)
	$(VENV)/bin/mypy
	$(VENV)/bin/pyright

check: lint typecheck

clean:
	rm -rf dist build *.egg-info .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -not -path './$(VENV)/*' -exec rm -rf {} +
