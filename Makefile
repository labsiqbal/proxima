.PHONY: dev build test doctor serve install-local

dev:
	bash scripts/dev

build:
	bash scripts/build

test:
	cd apps/api && .venv/bin/python -m pytest -q tests
	npm --prefix apps/web run build

doctor:
	bash scripts/proxima doctor

serve:
	bash scripts/proxima serve

install-local:
	sudo bash scripts/install-local
