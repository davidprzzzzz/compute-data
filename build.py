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
import csv, datetime as dt, html, json, re, shutil
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

# ---------------- historiques pour le chart
hs = [{"date": r["date"], "source": "ornn", "gpu": r["gpu"], "prix": float(r["prix"])} for r in hist("ornn_spot") if r.get("prix")]
hs += [{"date": r["date"], "source": "sd", "gpu": r["gpu"], "prix": float(r["prix"])} for r in hist("silicondata_spot") if r.get("prix")]
write("hist_spot.json", sorted(hs, key=lambda r: (r["source"], r["gpu"], r["date"])),
      [{"label": "Contenu", "value": "Historique quotidien des fixings Ornn OCPI et des indices Silicon Data, par GPU."}])
write("sd_term_hist.json", [{"date": r["date"], "mois": int(r["mois"]), "terme": float(r["terme"])} for r in hist("silicondata_term")],
      [{"label": "Contenu", "value": "Historique quotidien des taux de terme Silicon Data H100."}])

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

# ---------------- news : seed manuelle (data/static/news_seed.json) + flux automatique
nw = (rd(D / "history" / "news.json") or []) + (rd(ST / "news_seed.json") or [])
seen, nrows = set(), []
for i in sorted(nw, key=lambda i: i["date"], reverse=True):
    key = i["titre"].lower()
    if key in seen:
        continue
    seen.add(key)
    nrows.append({"id": str(len(nrows) + 1), "date": i["date"], "titre": i["titre"], "source": i.get("source", ""), "url": i.get("url", "")})
write("news.json", nrows[:60], [{"label": "Source", "value": "Google News (requêtes : GPU rental, H100 rental, Ornn, Silicon Data, Kalshi compute, futures compute, néoclouds) et sélection manuelle."}])

# ---------------- indice des prix affichés (Compute Group)
import statistics


def med(v):
    v = [x for x in v if x is not None]
    return round(statistics.median(v), 3) if v else None


lh = hist("listings")
# Méthode versionnée : toute modification du calcul est datée dans data/static/methode.json
METH = rd(ST / "methode.json") or []
METH_V = METH[-1]["version"] if METH else "v1"
BORNES = {"H100": (0.5, 15.0), "H200": (0.8, 20.0), "B200": (1.5, 30.0)}
EXCL_FMT = {"H100": {"PCIe", "NVL"}, "H200": {"NVL"}, "B200": set()}
HORS_DISPO = {"Out of stock", "Pre-order"}
# plateformes serverless / inférence : le prix inclut une couche logicielle, ce n'est pas de la location de GPU brute
SERVERLESS = {"replicate", "fal-ai", "cerebrium", "runware", "koyeb", "daytona", "beam", "huggingface", "lightning-ai", "modal", "baseten", "openrelay"}
OFFICIEL = {"lambda-labs": "list_lambda", "nebius": "list_nebius", "runpod": "list_runpod", "verda": "list_verda", "scaleway": "list_scaleway"}


def official_prices(day):
    """Prix en dollars lus sur les pages tarifs officielles archivées ce jour-là (ou la veille la plus proche)."""
    import gzip
    out = {}
    days = sorted((D / "archive").glob("*/*"), reverse=True)
    for slug, name in OFFICIEL.items():
        for dd in days:
            if f"{dd.parent.name}-{dd.name}" > day:
                continue
            f = dd / f"{name}.html.gz"
            if f.exists():
                t = gzip.open(f, "rt", errors="ignore").read()
                t = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)))
                out[slug] = ({float(x) for x in re.findall(r"\$\s*(\d{1,3}\.\d{1,4})", t)}, f"{dd.parent.name}-{dd.name}")
                break
    return out


idx_rows, checks = [], []
by = {}
for r in lh:
    if r.get("prix") in (None, ""):
        continue
    by.setdefault((r["date"], r["gpu"]), []).append(r)
prev = {}
for (d, g), all_rs in sorted(by.items()):
    lo, hi = BORNES.get(g, (0.1, 100))
    hors = [r for r in all_rs if not lo <= float(r["prix"]) <= hi]
    rs = [r for r in all_rs if lo <= float(r["prix"]) <= hi]

    def prov_med(filt):
        per = {}
        for r in rs:
            if filt(r):
                per.setdefault(r["slug"], []).append(float(r["prix"]))
        return [statistics.median(v) for v in per.values()]
    neo = lambda r: r["hyperscaler"] != "True" and r["slug"] not in SERVERLESS
    fmt = lambda r: r["format"] not in EXCL_FMT.get(g, set()) and r["slug"] not in SERVERLESS
    od = lambda r: r["facturation"] == "on-demand" and fmt(r)
    dispo = lambda r: r["dispo"] not in HORS_DISPO
    row = {"date": d, "gpu": g,
           "od_med": med(prov_med(lambda r: od(r) and neo(r))),
           "od_dispo": med(prov_med(lambda r: od(r) and neo(r) and dispo(r))),
           "n_od_dispo": len(prov_med(lambda r: od(r) and neo(r) and dispo(r))),
           "od_pcie": med(prov_med(lambda r: r["facturation"] == "on-demand" and r["format"] == "PCIe" and neo(r))) if g == "H100" else None,
           "od_eu": med(prov_med(lambda r: od(r) and r["europe"] == "True")),
           "od_hors_eu": med(prov_med(lambda r: od(r) and neo(r) and r["europe"] != "True")),
           "od_hyper": med(prov_med(lambda r: od(r) and not neo(r))),
           "n_od": len(prov_med(lambda r: od(r) and neo(r))),
           "spot_med": med(prov_med(lambda r: r["facturation"] == "spot" and fmt(r) and neo(r)))}
    for t in (1, 3, 6, 12, 24, 36):
        v = prov_med(lambda r, t=t: r["facturation"] == "réservé" and r["duree_mois"] == str(t) and fmt(r) and neo(r))
        row[f"r{t}"], row[f"n_r{t}"] = med(v), len(v)
    st = [r["dispo"] for r in rs if r["dispo"] in ("In stock", "Out of stock")]
    row["en_stock_pct"] = round(100 * st.count("In stock") / len(st), 1) if st else None
    row["n_fournisseurs"] = len({r["slug"] for r in rs})
    row["n_lignes"] = len(all_rs)

    # recoupement avec les pages officielles (on-demand et spot uniquement)
    offp = official_prices(d)
    ok = tot = 0
    for r in rs:
        if r["slug"] in offp and r["facturation"] in ("on-demand", "spot"):
            p = float(r["prix"])
            hit = any(abs(n - p) <= max(0.011, 0.005 * p) for n in offp[r["slug"]][0])
            tot += 1
            ok += hit
            checks.append({"date": d, "gpu": g, "fournisseur": r["fournisseur"], "config": r["config"], "facturation": r["facturation"],
                           "prix": round(p, 3), "page_officielle": offp[r["slug"]][1], "retrouve": "OUI" if hit else "NON"})
    row["recoup_ok"], row["recoup_n"] = ok, tot

    # contrôles automatiques
    motifs = []
    past = [x["n_lignes"] for x in prev.get(g, [])[-7:]]
    if past and row["n_lignes"] < 0.6 * statistics.median(past):
        motifs.append(f"lignes {row['n_lignes']} contre {statistics.median(past):.0f}")
    if row["n_od"] < 5:
        motifs.append(f"{row['n_od']} fournisseurs on-demand")
    if prev.get(g) and prev[g][-1]["od_med"] and row["od_med"]:
        v = row["od_med"] / prev[g][-1]["od_med"] - 1
        if abs(v) > 0.15:
            motifs.append(f"variation {100 * v:+.1f} % sur 1 relevé")
    if len(hors) > 0.1 * len(all_rs):
        motifs.append(f"{len(hors)} prix hors bornes")
    if tot >= 4 and ok / tot < 0.5:
        motifs.append(f"recoupement {ok}/{tot}")
    row["statut"] = "SUSPECT" if motifs else "OK"
    row["motif"] = " ; ".join(motifs)
    row["methode"] = METH_V
    prev.setdefault(g, []).append(row)
    idx_rows.append(row)
if idx_rows:
    write("list_index.json", idx_rows, [{"label": "Méthode", "value": f"Méthode {METH_V}, voir methode.json."}])
    write("list_checks.json", checks[-400:], [{"label": "Recoupement", "value": "Chaque prix GetDeploying on-demand ou spot de Lambda, Nebius, RunPod, Verda et Scaleway est recherché sur la page tarifs officielle archivée le même jour."}])
    last = max(r["date"] for r in idx_rows)
    h = next((r for r in idx_rows if r["date"] == last and r["gpu"] == "H100"), None)
    if h:
        lab = {1: "1M", 3: "3M", 6: "6M", 12: "1A", 24: "2A", 36: "3A"}
        lrows = [{"gpu": "H100", "mois": 0, "tenor": "On-demand", "mediane": h["od_med"], "q25": None, "q75": None, "n": h["n_od"], "fournisseurs": "", "date": last}]
        for t in (1, 3, 6, 12, 24, 36):
            if h[f"r{t}"] is not None:
                vals = []
                per = {}
                for r in by[(last, "H100")]:
                    if r["facturation"] == "réservé" and r["duree_mois"] == str(t) and r["hyperscaler"] != "True" and r["slug"] not in SERVERLESS and r["format"] not in EXCL_FMT["H100"] and 0.5 <= float(r["prix"]) <= 15:
                        per.setdefault(r["fournisseur"], []).append(float(r["prix"]))
                vals = sorted(statistics.median(v) for v in per.values())
                q = lambda p: round(vals[int((len(vals) - 1) * p)], 3) if len(vals) > 2 else None
                lrows.append({"gpu": "H100", "mois": t, "tenor": lab[t], "mediane": h[f"r{t}"], "q25": q(.25), "q75": q(.75), "n": h[f"n_r{t}"],
                              "fournisseurs": ", ".join(f"{k} {statistics.median(v):.2f}" for k, v in sorted(per.items())), "date": last})
        write("listes.json", lrows, [{"label": "Source", "value": "Indice Compute Group des prix affichés (voir list_index.json), H100, néoclouds hors hyperscalers."}])

if METH:
    write("methode.json", [{"version": m["version"], "depuis": m["depuis"], "changement": m["changement"]} for m in METH],
          [{"label": "Règle", "value": "Toute modification du calcul de l'indice est datée ici. L'historique est recalculé avec la version en vigueur."}])

# ---------------- statiques et références
if not (OUT / "listes.json").exists() or not idx_rows:
    shutil.copy(ST / "listes.json", OUT / "listes.json")
shutil.copy(ST / "agenda.csv", OUT / "agenda.csv")
fx = rd(D / "latest" / "fx.json") or {"date": "2026-11-18", "eurusd": 1.16}
refs = [{"id": "eurusd", "label": "EUR/USD (BCE)", "valeur": fx["eurusd"], "unite": "USD par EUR", "date": fx["date"]},
        {"id": "oi_perps", "label": "Open interest perpétuels H100 (5 venues)", "valeur": 158000, "unite": "USD", "date": "2026-11-18"}]
write("refs.json", refs)
(OUT / "manifest.json").write_text(json.dumps({"built": dt.datetime.now(dt.timezone.utc).isoformat(),
                                               "files": sorted(p.name for p in OUT.iterdir())}, indent=1))
print("ok", [p.name for p in OUT.iterdir()])
