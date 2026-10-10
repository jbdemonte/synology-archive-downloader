.DEFAULT_GOAL := help
PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python
VERSION ?= 1.0.2-2
SPK := dist/ArchiveStation-$(VERSION)-x86_64.spk

.PHONY: help build spk release runtime deps dev-deps run test test-ui screenshots lint format check clean
help:
	@printf '%s\n' 'Archive Station — Internet Archive Downloader' '' 'make build       Build the standalone DSM .spk (Python 3.12+)' 'make release     Test and prepare a GitHub release bundle from committed sources' 'make deps        Install Python runtime dependencies locally' 'make dev-deps    Install Python and frontend development tools' 'make run         Start the local app on 127.0.0.1:8274' 'make test        Run deterministic backend tests' 'make test-ui     Run isolated browser tests (npm + Chromium required)' 'make lint        Check Python and frontend formatting' 'make format      Format Python, HTML, CSS and JavaScript' 'make check       Run tests and lint' 'make clean       Remove build artifacts (keeps user data)'

$(PY):
	$(PYTHON) -m venv $(VENV)

deps: $(PY)
	$(PY) -m pip install -r requirements.txt

dev-deps: $(PY)
	$(PY) -m pip install -r requirements-dev.txt
	npm ci
	npx playwright install chromium

runtime:
	$(PYTHON) scripts/fetch_runtime.py

build: runtime
	$(PYTHON) scripts/build_spk.py --runtime build/cache/python-runtime.tar.gz --waitress-wheel build/cache/waitress-3.0.2-py3-none-any.whl --version $(VERSION) --output $(SPK)

spk: build

release:
	$(PYTHON) scripts/release.py --version "$(VERSION)"

run: deps
	PYTHONPATH=src $(PY) -m archive_station

test: $(PY)
	PYTHONPATH=src $(PY) -m unittest discover -s tests -v

test-ui: $(PY)
	$(PY) scripts/test_ui.py

screenshots: $(PY)
	$(PY) scripts/test_ui.py --screenshots

lint: $(PY)
	$(VENV)/bin/ruff check src tests scripts packaging/synology/ui/gateway.cgi
	$(VENV)/bin/ruff format --check src tests scripts packaging/synology/ui/gateway.cgi
	npm run format:check
	node --check src/archive_station/static/app.js
	node --check src/archive_station/static/i18n.js

format: $(PY)
	$(VENV)/bin/ruff check src tests scripts packaging/synology/ui/gateway.cgi --fix
	$(VENV)/bin/ruff format src tests scripts packaging/synology/ui/gateway.cgi
	npm run format

check: test lint

clean:
	rm -rf build dist .ruff_cache
