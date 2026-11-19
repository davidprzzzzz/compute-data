"""Prépare le site autonome (site/) : copie les fichiers de dash/ dans site/data/.

La page site/index.html est la même que celle du dashboard Claude, sans le journal des cotations
(données privées qui restent dans Claude), servie avec site/shim.js qui remplace le moteur du dashboard.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
out = ROOT / "site" / "data"
out.mkdir(parents=True, exist_ok=True)
for p in (ROOT / "dash").iterdir():
    if p.suffix in (".json", ".csv"):
        shutil.copy(p, out / p.name)
print("site/data :", sorted(p.name for p in out.iterdir()))
