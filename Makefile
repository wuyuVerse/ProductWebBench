.PHONY: install validate export verify test lint clean

install:            ## editable install
	python3 -m pip install -e ".[dev]"

validate:           ## schema-check all 400 frozen tasks
	pwb validate-tasks tasks

export:             ## export the suite as Harbor task directories: make export OUT=path
	pwb export-harbor-tasks --out "$(OUT)"

verify:             ## re-verify a submission directory: make verify SUB=path/to/run
	pwb verify-submission --submission "$(SUB)"

test:
	pytest -q

lint:
	ruff check productwebbench

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
