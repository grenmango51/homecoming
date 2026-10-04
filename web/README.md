# Flight Finder UI

The compact UI is served by `local_app.py`, which runs both local scrapers for new prices. Open it through `start-local.cmd` on Windows, or `python local_app.py --open` in the project virtual environment.

Do not host this folder as a static site: it needs the authenticated localhost `/api` endpoints to start searches. See [the local app guide](../docs/WEB_UI.md) for installation, privacy and provider limitations.
