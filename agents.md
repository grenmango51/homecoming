# Agent Instructions

To run daily flight searches (such as the HEL to HAN route), execute `.\.venv\Scripts\python.exe run_all.py --trip HEL_HAN.toml` to scrape Google Flights and Skyscanner in parallel and automatically generate comparison reports. Route configurations reside in `config/` (e.g., `config/HEL_HAN.toml`), while general execution settings are managed in `config/config.toml`.
