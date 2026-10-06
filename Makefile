.PHONY: setup demo serve check
RUN = uv run --no-editable
setup:
	uv sync --locked --no-editable
	$(RUN) motionlab setup
demo: setup
	$(RUN) motionlab demo-video
	$(RUN) motionlab serve
serve:
	$(RUN) motionlab serve
check:
	$(RUN) ruff check .
	$(RUN) ruff format --check src tests
	$(RUN) pytest -q
	node --check src/motionlab/static/app.js
