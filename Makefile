.PHONY: install reproduce figures analyses catalog verify test lint clean

install:            ## editable install with the analysis extras
	python3 -m pip install -e ".[analysis,dev]"

reproduce:          ## regenerate every paper number and figure from the cache
	bash tools/reproduce.sh

analyses:
	bash tools/reproduce.sh analyses

figures:
	bash tools/reproduce.sh figures

catalog:            ## list the 400 frozen tasks
	pwb catalog

verify:             ## re-verify a submission directory: make verify SUB=path/to/run
	pwb verify-submission --submission "$(SUB)"

test:
	pytest -q

lint:
	ruff check productwebbench

clean:
	rm -rf figures/*.pdf figures/png __pycache__ .pytest_cache .ruff_cache
