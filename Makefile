lint:
	@uv run ruff check
	@uv run ruff format --check

.PHONY: lint

typecheck:
	@uv run pyrefly check src

.PHONY: typecheck

test:
	@uv run pytest tests

.PHONY: test

ci: lint typecheck test
.PHONY: ci