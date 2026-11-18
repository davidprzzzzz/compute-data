# compute-data

Collecte quotidienne des prix du compute GPU pour l'écran **Compute Group — Run GPU**.

- `fetch.py` : Kalshi (API publique, séries `KX<GPU>MS`), Ornn OCPI, Silicon Data, EUR/USD, aicomputetracker.
- `build.py` : construit `dash/*.json` lus par le dashboard.
- `.github/workflows/fetch.yml` : tous les jours à 05:17 UTC, et à chaque modification du code.
- `data/history/*.csv` : historique quotidien. `data/raw/` : pages brutes du dernier passage. `data/log.txt` : journal.
