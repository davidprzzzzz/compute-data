# compute-data

Collecte quotidienne des prix du compute GPU pour l'écran **Compute Group — Run GPU**.

- `fetch.py` : Kalshi (API publique, séries `KX<GPU>MS`), Ornn OCPI, Silicon Data, EUR/USD, aicomputetracker.
- `build.py` : construit `dash/*.json` lus par le dashboard.
- `.github/workflows/fetch.yml` : toutes les 4 heures, et à chaque modification du code.
- `data/history/*.csv` : historique quotidien, jamais écrasé (Silicon Data : les 30 jours de chaque page sont fusionnés à chaque passage).
- `data/history/kalshi_snapshots.csv` : tous les relevés Kalshi ; `kalshi_forwards.csv` garde par jour le relevé le plus coté.
- `data/archive/AAAA-MM/JJ/` : relevés bruts compressés (Kalshi à chaque passage, pages Ornn et Silicon Data une fois par jour).
- `data/log.txt` : journal du dernier passage.
