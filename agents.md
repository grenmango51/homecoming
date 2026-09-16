# Agent Instructions

## Daily fare scan

Run both scrapers in parallel and generate the comparison report:

```
.\.venv\Scripts\python.exe run_all.py --trip HEL_HAN.toml
```

Route and date settings live in `config/<ROUTE>.toml` (e.g. `config/HEL_HAN.toml`);
execution settings live in `config/config.toml`.

## Checks before committing

```
python -m unittest discover -s tests -t .
ruff check .
```

The test suite imports only the browser-free parsing modules
(`src/google_parse.py`, `src/skyscanner_parse.py`, `src/common.py`,
`src/reporting.py`, `src/analysis.py`), so it runs without Playwright installed.
Keep it that way: put pure logic in those modules and Playwright-dependent page
driving in `src/browser.py`.
