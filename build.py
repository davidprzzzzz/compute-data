"""Construit les fichiers lus par l'écran « Compute Group — Run GPU » à partir de data/.

dash/spot.json        un rang par GPU : Ornn et Silicon Data côte à côte, avec Δ 1j / 1m / 3m
dash/kalshi.json      dernier forward implicite par GPU et par mois de livraison
dash/kalshi_hist.json historique quotidien des forwards bornés (pour les Δ à maturité constante)
dash/sd_term.json     structure par terme Silicon Data (H100) avec Δ
dash/listes.json      médianes des prix réservés publiés par durée (statique, relevé manuel)
dash/refs.json        EUR/USD et références
dash/agenda.csv       agenda marché (statique)
Si une source n'a rien rendu, on garde le dernier fichier de dash/ (ou la graine data/static/seed_*.json).
"""
import csv, datetime as dt, json, shutil
from pathlib import Path

ROOT = Path(__file__).parent
D, OUT, ST = ROOT / "data", ROOT / "dash", ROOT / "data" / "static"
OUT.mkdir(exist_ok=True)
GPUS = ["H100", "H200", "B200", "B300", "A100"]


def rd(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def hist(name):
    p = D / "history" / f"{name}.csv"
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


def back(series, date, days, tol=6):
    """Valeur la plus proche de date - days (au plus tol jours d'écart) dans une série {date: valeur}."""
    target = dt.date.fromisoformat(date) - dt.timedelta(days=days)
    best = None
    for d, v in series.items():
        gap = abs((dt.date.fromisoformat(d) - target).days)
        if gap <= tol and (best is None or gap < best[0]):
            best = (gap, v)
    return None if best is None else best[1]


def chg(now, old):
    return None if now is None or old in (None, 0) else round((now / old - 1) * 100, 2)


def write(name, rows, meta=None):
    (OUT / name).write_text(json.dumps({"rows": rows, "meta": meta or []}, ensure_ascii=False, indent=1))


def series_by_gpu(rows):
    out = {}
    for r in rows:
        if r.get("prix") not in (None, ""):
            out.setdefault(r["gpu"], {})[r["date"]] = float(r["prix"])
    return out


# ---------------- spot
orn = rd(D / "latest" / "ornn.json") or {}
sdl = rd(D / "latest" / "silicondata.json") or {}
seed = rd(ST / "seed_spot.json") or {"rows": []}
oh, sh = series_by_gpu(hist("ornn_spot")), series_by_gpu(hist("silicondata_spot"))
spot = []
for g in GPUS:
    o = next((r for r in orn.get("rows", []) if r["gpu"] == g), None)
    s = next((r for r in sdl.get("spot", []) if r["gpu"] == g), None)
    sd_seed = next((r for r in seed["rows"] if r["gpu"] == g), {})
    row = {"gpu": g}
    if o:
        oser = oh.get(g, {})
        row.update(ornn=o["prix"], ornn_date=o["date"], ornn_1j=o.get("var_1j"), ornn_1m=o.get("var_1m"),
                   ornn_3m=chg(o["prix"], back(oser, o["date"], 91)))
    else:
        row.update({k: sd_seed.get(k) for k in ("ornn", "ornn_date", "ornn_1j", "ornn_1m", "ornn_3m")})
    if s:
        ser = sh.get(g, {})
        row.update(sd=s["prix"], sd_date=s["date"], sd_1j=chg(s["prix"], back(ser, s["date"], 1, 3)),
                   sd_1m=chg(s["prix"], back(ser, s["date"], 30)), sd_3m=chg(s["prix"], back(ser, s["date"], 91)))
    else:
        row.update({k: sd_seed.get(k) for k in ("sd", "sd_date", "sd_1j", "sd_1m", "sd_3m")})
    spot.append(row)
write("spot.json", spot, [{"label": "Sources", "value": "Ornn OCPI : data.ornn.com/markets (Δ 1j et 1m publiés par Ornn ; Δ 3m calculé sur l'historique). Silicon Data : silicondata.com/products/silicon-index/<gpu> (Δ calculés sur l'historique)."}])

# ---------------- Kalshi
kl = rd(D / "latest" / "kalshi.json")
if kl and kl.get("rows"):
    asof = dt.date.fromisoformat(kl["date"])
    rows = []
    for r in kl["rows"]:
        y, m = map(int, r["mois"].split("-"))
        k = (y - asof.year) * 12 + (m - asof.month)
        if k < 0:
            continue
        rows.append({"cle": f'{r["gpu"]}-{r["mois"]}', "gpu": r["gpu"], "mois": r["mois"], "k": k, "prix": r["prix"],
                     "borne": r["borne"], "q25": r["q25"], "q75": r["q75"], "qualite": r["qualite"],
                     "deux_sens": r["deux_sens"], "spread": r["spread_med"], "strikes": r["strikes"], "event": r["event"], "date": kl["date"]})
    rows.sort(key=lambda r: (GPUS.index(r["gpu"]) if r["gpu"] in GPUS else 9, r["mois"]))
    write("kalshi.json", rows, [{"label": "Méthode", "value": "Pour chaque mois de livraison, médiane de la distribution implicite de l'échelle de contrats Kalshi (seuils lissés par régression isotone), quartiles en fourchette. Borne « >= » / « <= » : la médiane tombe hors des strikes cotés. Source : api.elections.kalshi.com, séries KX<GPU>MS."}])
elif not (OUT / "kalshi.json").exists() and (ST / "seed_kalshi.json").exists():
    shutil.copy(ST / "seed_kalshi.json", OUT / "kalshi.json")

kh = [r for r in hist("kalshi_forwards") if r.get("borne", "") == "" and r.get("prix") not in (None, "")]
write("kalshi_hist.json", [{"date": r["date"], "gpu": r["gpu"], "mois": r["mois"], "prix": float(r["prix"])} for r in kh],
      [{"label": "Contenu", "value": "Historique quotidien des forwards Kalshi bornés (médiane implicite), reconstruit à partir des chandeliers journaliers de chaque contrat puis complété chaque jour."}])

# ---------------- Silicon Data terme
term = (sdl.get("terme") or [])
th = hist("silicondata_term")
tser = {}
for r in th:
    tser.setdefault(int(r["mois"]), {})[r["date"]] = float(r["terme"])
trows = []
for r in term:
    ser = tser.get(r["mois"], {})
    trows.append({"mois": r["mois"], "terme": r["terme"], "forward": r["forward"], "date": r["date"],
                  "var_1j": chg(r["terme"], back(ser, r["date"], 1, 3)), "var_1m": chg(r["terme"], back(ser, r["date"], 30)),
                  "var_3m": chg(r["terme"], back(ser, r["date"], 91))})
if trows:
    write("sd_term.json", trows, [{"label": "Source", "value": "silicondata.com/products/forward-curve (H100, taux de terme et forward implicite)."}])
elif not (OUT / "sd_term.json").exists() and (ST / "seed_sd_term.json").exists():
    shutil.copy(ST / "seed_sd_term.json", OUT / "sd_term.json")

# ---------------- statiques et références
shutil.copy(ST / "listes.json", OUT / "listes.json")
shutil.copy(ST / "agenda.csv", OUT / "agenda.csv")
fx = rd(D / "latest" / "fx.json") or {"date": "2026-11-18", "eurusd": 1.16}
refs = [{"id": "eurusd", "label": "EUR/USD (BCE)", "valeur": fx["eurusd"], "unite": "USD par EUR", "date": fx["date"]},
        {"id": "oi_perps", "label": "Open interest perpétuels H100 (5 venues)", "valeur": 158000, "unite": "USD", "date": "2026-11-18"}]
write("refs.json", refs)
(OUT / "manifest.json").write_text(json.dumps({"built": dt.datetime.now(dt.timezone.utc).isoformat(),
                                               "files": sorted(p.name for p in OUT.iterdir())}, indent=1))
print("ok", [p.name for p in OUT.iterdir()])
