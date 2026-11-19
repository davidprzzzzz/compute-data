"""Compute Group — collecte quotidienne des prix du compute GPU.

Sources :
  - Kalshi (API publique) : échelles de contrats mensuels réglés sur l'indice Ornn,
    séries KX{GPU}MS. On reconstruit le forward implicite de chaque mois (médiane
    de la distribution implicite) et sa fourchette (quartiles).
  - Ornn OCPI : fixings spot quotidiens (data.ornn.com).
  - Silicon Data : indices de location quotidiens et taux de terme H100.
  - aicomputetracker.com : courbe Kalshi reconstruite (contrôle croisé).

Écrit :
  data/latest/*.json        dernier relevé, par source
  data/history/*.csv        historique quotidien (une ligne par point)
  data/raw/*                pages brutes du dernier relevé (pour déboguer les parseurs)
  data/log.txt              journal du dernier passage
"""
import csv, datetime as dt, html, json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).parent
D = ROOT / "data"
for sub in ("latest", "history", "raw"):
    (D / sub).mkdir(parents=True, exist_ok=True)
TODAY = dt.datetime.now(dt.timezone.utc).date().isoformat()
LOG = []
UA = {"User-Agent": "Mozilla/5.0 (compute-group-desk; +https://compute-group.com)", "Accept": "*/*"}
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GPUS = ["H100", "H200", "B200", "B300", "A100"]
MON = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def get(url, as_json=False, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + 2 * i)
                continue
            log("HTTP", e.code, url)
            return None
        except Exception as e:  # noqa
            log("ERR", type(e).__name__, str(e)[:120], url)
            time.sleep(1 + i)
    return None


def append_hist(name, rows, keys):
    """Ajoute des lignes à data/history/<name>.csv en remplaçant celles du même jour et de la même clé."""
    p = D / "history" / f"{name}.csv"
    old = []
    if p.exists():
        with p.open() as f:
            old = list(csv.DictReader(f))
    norm = lambda r: tuple("" if r.get(k) is None else str(r.get(k)) for k in keys)  # None et "" doivent donner la même clé
    rows = [{k: ("" if v is None else v) for k, v in r.items()} for r in rows]
    new_keys = {norm(r) for r in rows}
    kept = [r for r in old if norm(r) not in new_keys]
    seen, uniq = set(), []
    for r in rows:  # une configuration listée deux fois à l'identique ne compte qu'une fois
        t = tuple(sorted((k, str(v)) for k, v in r.items()))
        if t not in seen:
            seen.add(t)
            uniq.append(r)
    allr = kept + uniq
    cols = []
    for r in (old[:1] + rows):
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    allr.sort(key=lambda r: tuple(str(r.get(k, "")) for k in keys))
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(allr)
    return len(allr)


# ---------------------------------------------------------------- Kalshi
def cents(m, key):
    """Prix d'un contrat en probabilité 0..1, quel que soit le format de l'API (cents ou dollars)."""
    for k in (key + "_dollars", key):
        v = m.get(k)
        if v is None or v == "":
            continue
        v = float(v)
        return v if k.endswith("_dollars") else v / 100.0
    return None


def event_month(event_ticker):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})?$", event_ticker)
    if not m:
        return None
    return f"20{m.group(1)}-{MON[m.group(2)]:02d}"


def pava_decreasing(xs):
    """Régression isotone décroissante (pool adjacent violators) sur une liste de (poids, valeur)."""
    blocks = []
    for w, v in xs:
        blocks.append([w, v, 1])
        while len(blocks) > 1 and blocks[-2][1] < blocks[-1][1]:
            w2, v2, n2 = blocks.pop()
            w1, v1, n1 = blocks.pop()
            blocks.append([w1 + w2, (w1 * v1 + w2 * v2) / (w1 + w2), n1 + n2])
    out = []
    for w, v, n in blocks:
        out += [v] * n
    return out


def survival_points(markets):
    """Transforme une échelle de marchés en points (strike, P(indice > strike)).

    Gère les deux formes d'échelle Kalshi : seuils « au-dessus de K » et tranches [a, b).
    Renvoie aussi le nombre de cotations à deux sens et l'écart médian bid/ask.
    """
    pts, spreads, two = [], [], 0
    above = [m for m in markets if m.get("strike_type") in ("greater", "greater_or_equal")]
    buckets = [m for m in markets if m.get("strike_type") == "between"]
    def mid(m):
        nonlocal two
        b, a, last = cents(m, "yes_bid"), cents(m, "yes_ask"), cents(m, "last_price")
        if b is not None and a is not None and 0 < a <= 1 and a >= b and (b > 0 or a < 1):
            if b > 0 and a < 1:
                two += 1
                spreads.append(a - b)
            return (b + a) / 2
        return last
    if above:
        for m in above:
            k, p = m.get("floor_strike"), mid(m)
            if k is not None and p is not None:
                pts.append((float(k), p))
    elif buckets:
        rows = []
        for m in buckets:
            lo, hi, p = m.get("floor_strike"), m.get("cap_strike"), mid(m)
            if p is not None:
                rows.append((float(lo) if lo is not None else None, float(hi) if hi is not None else None, p))
        for m in markets:
            if m.get("strike_type") == "less":
                p = mid(m)
                if p is not None and m.get("cap_strike") is not None:
                    rows.append((None, float(m["cap_strike"]), p))
        tot = sum(r[2] for r in rows) or 1.0
        rows = [(a, b, p / tot) for a, b, p in rows]
        edges = sorted({b for a, b, p in rows if b is not None})
        for e in edges:
            pts.append((e, sum(p for a, b, p in rows if a is not None and a >= e) +
                        sum(p for a, b, p in rows if b is not None and a is not None and a < e < b) * 0.5))
    pts.sort()
    if not pts:
        return [], two, None
    smooth = pava_decreasing([(1.0, p) for _, p in pts])
    pts = [(k, max(0.0, min(1.0, s))) for (k, _), s in zip(pts, smooth)]
    spreads.sort()
    return pts, two, (spreads[len(spreads) // 2] if spreads else None)


def quantile_from_survival(pts, q):
    """Strike où P(indice > K) = 1 - q. Renvoie (valeur, borne) avec borne '' / '>=' / '<='."""
    target = 1 - q
    if pts[0][1] < target:
        return pts[0][0], "<="
    if pts[-1][1] > target:
        return pts[-1][0], ">="
    for (k1, s1), (k2, s2) in zip(pts, pts[1:]):
        if s1 >= target >= s2:
            if s1 == s2:
                return (k1 + k2) / 2, ""
            return k1 + (s1 - target) * (k2 - k1) / (s1 - s2), ""
    return None, "?"


def implied(markets):
    pts, two, spr = survival_points(markets)
    if len(pts) < 2:
        return None
    med, b = quantile_from_survival(pts, 0.5)
    q25, b25 = quantile_from_survival(pts, 0.25)
    q75, b75 = quantile_from_survival(pts, 0.75)
    if b:
        qual = "borne"
    elif two >= 3 and (spr is not None and spr <= 0.15) and not b25 and not b75:
        qual = "bon"
    else:
        qual = "mince"
    return {"prix": round(med, 4), "borne": b, "q25": None if b25 else round(q25, 4), "q75": None if b75 else round(q75, 4),
            "strikes": len(pts), "deux_sens": two, "spread_med": None if spr is None else round(spr, 3), "qualite": qual,
            "strike_min": pts[0][0], "strike_max": pts[-1][0]}


def kalshi_series_markets(series):
    out, cursor = [], None
    for status in ("open", "unopened"):
        cursor = None
        for _ in range(10):
            url = f"{KALSHI}/markets?series_ticker={series}&status={status}&limit=1000" + (f"&cursor={cursor}" if cursor else "")
            j = get(url, as_json=True)
            if not j:
                break
            out += j.get("markets", [])
            cursor = j.get("cursor")
            if not cursor:
                break
    return out


KEEP = ("ticker", "event_ticker", "strike_type", "floor_strike", "cap_strike", "yes_bid", "yes_ask", "last_price",
        "yes_bid_dollars", "yes_ask_dollars", "last_price_dollars", "volume", "open_interest", "close_time")
NOW = dt.datetime.now(dt.timezone.utc)
ARCH = D / "archive" / NOW.strftime("%Y-%m") / NOW.strftime("%d")


def archive(name, text):
    """Copie compressée et horodatée d'un relevé brut : data/archive/AAAA-MM/JJ/<nom>."""
    import gzip
    ARCH.mkdir(parents=True, exist_ok=True)
    with gzip.open(ARCH / (name + ".gz"), "wt", encoding="utf-8") as f:
        f.write(text)


def kalshi():
    """Relevé des échelles Kalshi. Chaque relevé est archivé ; le forward du jour retenu est celui
    du relevé qui a le plus de cotations à deux sens pour ce mois (les carnets se vident à certaines heures)."""
    snap, raw = [], {}
    ts = NOW.strftime("%Y-%m-%dT%H:%MZ")
    for g in GPUS:
        series = f"KX{g}MS"
        mk = [{k: m.get(k) for k in KEEP if m.get(k) is not None} for m in kalshi_series_markets(series)]
        raw[series] = mk
        log("Kalshi", series, "marchés:", len(mk))
        by_event = {}
        for m in mk:
            by_event.setdefault(m.get("event_ticker", ""), []).append(m)
        for ev, ms in sorted(by_event.items()):
            month = event_month(ev)
            r = implied(ms)
            if month and r:
                snap.append({"date": TODAY, "ts": ts, "gpu": g, "mois": month, "event": ev, **r})
    body = json.dumps({"ts": ts, "series": raw})
    (D / "raw" / "kalshi_markets.json").write_text(body)
    archive(f"kalshi_{NOW.strftime('%H%M')}.json", body)
    if not snap:
        return raw
    # tous les relevés, sans sélection
    append_hist("kalshi_snapshots", [{k: r.get(k) for k in ("ts", "gpu", "mois", "prix", "borne", "q25", "q75", "qualite", "deux_sens", "spread_med", "strikes")}
                                     for r in snap], ["ts", "gpu", "mois"])
    # meilleur relevé du jour par (gpu, mois)
    lp = D / "latest" / "kalshi.json"
    prev = json.loads(lp.read_text()) if lp.exists() else {}
    best = {(r["gpu"], r["mois"]): r for r in prev.get("rows", []) if prev.get("date") == TODAY}
    for r in snap:
        old = best.get((r["gpu"], r["mois"]))
        if old is None or (r["deux_sens"] or 0) >= (old.get("deux_sens") or 0):
            best[(r["gpu"], r["mois"])] = r
    rows = sorted(best.values(), key=lambda r: (r["gpu"], r["mois"]))
    lp.write_text(json.dumps({"date": TODAY, "rows": rows}, indent=1))
    n = append_hist("kalshi_forwards", [{"date": TODAY, "gpu": r["gpu"], "mois": r["mois"], "prix": r["prix"], "borne": r["borne"],
                                        "q25": r["q25"], "q75": r["q75"], "qualite": r["qualite"], "deux_sens": r["deux_sens"], "ts": r["ts"]}
                                       for r in rows], ["date", "gpu", "mois"])
    log("Kalshi historique:", n, "lignes ; relevé", ts)
    return raw


def archive_daily():
    """Une copie compressée par jour des pages brutes Ornn, Silicon Data et aicomputetracker."""
    for p in (D / "raw").iterdir():
        if p.name.startswith("kalshi_"):
            continue
        if not (ARCH / (p.name + ".gz")).exists():
            archive(p.name, p.read_text())


def kalshi_backfill(raw, days=200):
    """Reconstruit l'historique quotidien des forwards à partir des chandeliers de chaque contrat."""
    p = D / "history" / "kalshi_forwards.csv"
    have = set()
    if p.exists():
        with p.open() as f:
            have = {r["date"] for r in csv.DictReader(f)}
    if len(have) > 20:
        log("Backfill Kalshi déjà fait (", len(have), "dates )")
        return
    end = int(time.time())
    start = end - days * 86400
    per_day = {}  # (date, gpu, event) -> list of synthetic markets
    calls = 0
    for series, mk in raw.items():
        g = series[2:-2]
        if not isinstance(mk, list):
            continue
        for m in mk:
            t = m.get("ticker")
            url = f"{KALSHI}/series/{series}/markets/{t}/candlesticks?start_ts={start}&end_ts={end}&period_interval=1440"
            j = get(url, as_json=True)
            calls += 1
            time.sleep(0.08)
            if not j:
                continue
            for c in j.get("candlesticks", []):
                day = dt.datetime.fromtimestamp(c["end_period_ts"], dt.timezone.utc).date().isoformat()
                def close(side):
                    v = c.get(side) or {}
                    for k in ("close_dollars", "close"):
                        if v.get(k) not in (None, ""):
                            x = float(v[k])
                            return x * 100 if k.endswith("_dollars") else x
                    return None
                syn = dict(m)
                syn["yes_bid"], syn["yes_ask"], syn["last_price"] = close("yes_bid"), close("yes_ask"), close("price")
                for k in ("yes_bid_dollars", "yes_ask_dollars", "last_price_dollars"):
                    syn.pop(k, None)
                per_day.setdefault((day, g, m.get("event_ticker", "")), []).append(syn)
    rows = []
    for (day, g, ev), ms in per_day.items():
        month = event_month(ev)
        r = implied(ms)
        if month and r and day < TODAY:
            rows.append({"date": day, "gpu": g, "mois": month, "prix": r["prix"], "borne": r["borne"],
                         "q25": r["q25"], "q75": r["q75"], "qualite": r["qualite"]})
    log("Backfill Kalshi:", calls, "appels,", len(rows), "points")
    if rows:
        append_hist("kalshi_forwards", rows, ["date", "gpu", "mois"])


# ---------------------------------------------------------------- Ornn
def text_of(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h))


ORNN_NAMES = {"H100": r"H100(?: SXM)?", "H200": r"H200", "B200": r"B200", "B300": r"B300", "A100": r"A100(?: SXM4)?"}


def num(s):
    return float(s.replace("−", "-").replace("+", "").replace(",", ""))


def ornn():
    h = get("https://data.ornn.com/markets")
    if not h:
        return
    (D / "raw" / "ornn_markets.html").write_text(h)
    t = text_of(h)
    (D / "raw" / "ornn_markets.txt").write_text(t)
    settle = None
    m = re.search(r"(?:Latest settlement|[Ss]ettled)\s*:?\s*([A-Z][a-z]+) (\d{1,2}),? (\d{4})", t)
    if m:
        try:
            settle = dt.datetime.strptime(f"{m.group(1)[:3]} {m.group(2)} {m.group(3)}", "%b %d %Y").date().isoformat()
        except ValueError:
            settle = None
    rows = []
    for g, pat in ORNN_NAMES.items():
        m = re.search(pat + r"\s*(?:Bloomberg:\s*\S+\s*)?\$\s*([\d.]+)\s*([+\-−]?[\d.]+)%\s*([+\-−]?[\d.]+)%", t)
        if m:
            rows.append({"date": settle or TODAY, "gpu": g, "prix": num(m.group(1)), "var_1j": num(m.group(2)), "var_1m": num(m.group(3))})
    log("Ornn:", len(rows), "GPU, settle", settle)
    (D / "latest" / "ornn.json").write_text(json.dumps({"date": settle, "fetched": TODAY, "rows": rows}, indent=1))
    if rows:
        append_hist("ornn_spot", [{k: r[k] for k in ("date", "gpu", "prix")} for r in rows], ["date", "gpu"])


# ---------------------------------------------------------------- Silicon Data
SD_PAGES = {"H100": "h100", "H200": "h200", "B200": "b200", "A100": "a100"}


def silicon():
    rows, latest = [], []
    for g, slug in SD_PAGES.items():
        h = get(f"https://www.silicondata.com/products/silicon-index/{slug}")
        if not h:
            continue
        (D / "raw" / f"silicondata_{slug}.html").write_text(h)
        t = text_of(h)
        pts = {}
        for m in re.finditer(r"([A-Z][a-z]{2} \d{1,2}, \d{4})\s*\|?\s*\$?([\d]+\.[\d]+)", t):
            try:
                d = dt.datetime.strptime(m.group(1), "%b %d, %Y").date().isoformat()
            except ValueError:
                continue
            v = float(m.group(2))
            if 0.2 < v < 50:
                pts[d] = v
        for d, v in pts.items():
            rows.append({"date": d, "gpu": g, "prix": v})
        if pts:
            d = max(pts)
            latest.append({"gpu": g, "date": d, "prix": pts[d]})
        log("Silicon Data", g, len(pts), "points", (max(pts) if pts else ""))
    if rows:
        append_hist("silicondata_spot", rows, ["date", "gpu"])
    term = []
    h = get("https://www.silicondata.com/products/forward-curve")
    if h:
        (D / "raw" / "silicondata_forward.html").write_text(h)
        t = text_of(h)
        asof = None
        m = re.search(r"Updated ([A-Z][a-z]{2} \d{1,2}, \d{4})", t)
        if m:
            asof = dt.datetime.strptime(m.group(1), "%b %d, %Y").date().isoformat()
        for m in re.finditer(r"\|\s*(\d{1,2})\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|", t):
            term.append({"date": asof or TODAY, "gpu": "H100", "mois": int(m.group(1)), "terme": float(m.group(2)), "forward": float(m.group(3))})
        if not term:
            for m in re.finditer(r"\b(\d{1,2})\s+([1-9]\.\d{2})\s+([1-9]\.\d{2})\b", t):
                term.append({"date": asof or TODAY, "gpu": "H100", "mois": int(m.group(1)), "terme": float(m.group(2)), "forward": float(m.group(3))})
        log("Silicon Data terme:", len(term), "points, au", asof)
        if term:
            append_hist("silicondata_term", term, ["date", "gpu", "mois"])
    (D / "latest" / "silicondata.json").write_text(json.dumps({"fetched": TODAY, "spot": latest, "terme": term}, indent=1))


# ---------------------------------------------------------------- EUR/USD (BCE via frankfurter.app)
def fx():
    j = get("https://api.frankfurter.app/latest?from=EUR&to=USD", as_json=True)
    if j and "rates" in j:
        row = {"date": j.get("date", TODAY), "eurusd": j["rates"]["USD"]}
        (D / "latest" / "fx.json").write_text(json.dumps(row))
        append_hist("fx", [row], ["date"])
        log("EUR/USD", row)


# ---------------------------------------------------------------- News (Google News RSS)
NEWS_QUERIES = ['"GPU rental" price', '"H100" rental price', 'Ornn compute', '"Silicon Data" GPU index',
                'Kalshi compute GPU', 'GPU compute futures', 'neocloud GPU capacity contract', 'H200 OR B200 rental price']


def news():
    import email.utils, xml.etree.ElementTree as ET
    p = D / "history" / "news.json"
    items = json.loads(p.read_text()) if p.exists() else []
    seen = {i["url"] for i in items} | {i["titre"].lower() for i in items}
    added = 0
    for q in NEWS_QUERIES:
        url = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=" + urllib.request.quote(q + " when:14d")
        x = get(url)
        if not x:
            continue
        try:
            root = ET.fromstring(x)
        except ET.ParseError:
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            src = (it.findtext("source") or "").strip()
            pub = it.findtext("pubDate")
            try:
                d = email.utils.parsedate_to_datetime(pub).date().isoformat()
            except Exception:
                d = TODAY
            if src and title.endswith(" - " + src):
                title = title[: -len(" - " + src)]
            if not title or link in seen or title.lower() in seen:
                continue
            seen |= {link, title.lower()}
            items.append({"date": d, "titre": title, "source": src, "url": link, "requete": q})
            added += 1
    items.sort(key=lambda i: i["date"], reverse=True)
    items = items[:400]
    p.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    log("News:", added, "nouvelles,", len(items), "au total")


# ---------------------------------------------------------------- prix affichés (pages publiques)
LIST_PAGES = {
    "getdeploying_h100": "https://getdeploying.com/gpus/nvidia-h100",
    "getdeploying_h200": "https://getdeploying.com/gpus/nvidia-h200",
    "getdeploying_b200": "https://getdeploying.com/gpus/nvidia-b200",
    "nebius": "https://nebius.com/prices",
    "lambda": "https://lambda.ai/pricing",
    "scaleway": "https://www.scaleway.com/en/pricing/gpu/",
    "verda": "https://verda.com/pricing",
    "runpod": "https://www.runpod.io/pricing",
}


EUROPE = {"scaleway", "ovh", "verda", "nebius", "seeweb", "leaf-cloud", "sesterce", "upcloud", "gcore", "cloudferro", "lyceum",
          "impossible-cloud", "hyperstack", "greenai-cloud", "leaseweb", "beyond-pl", "hostinger"}  # siège en Europe
HYPERSCALERS = {"aws", "microsoft-azure", "google-cloud", "oracle-cloud"}


def billing(b):
    b = (b or "").strip()
    if b.lower().startswith("on-demand"):
        return "on-demand", 0
    if b.lower().startswith("spot"):
        return "spot", 0
    m = re.match(r"Reserved \((\d+)mo\)", b)
    if m:
        return "réservé", int(m.group(1))
    if b.lower().startswith("reserved"):
        return "réservé", None
    return "autre", None


def parse_getdeploying(h, gpu):
    """Une ligne par configuration affichée : fournisseur, format, nb de GPU, type de facturation, durée, prix par GPU-heure, disponibilité."""
    rows = []
    parts = re.split(r'<div id="configs-([a-z0-9\-]+)" data-configs', h)
    for k in range(1, len(parts), 2):
        slug, body = parts[k], parts[k + 1]
        names = re.findall(r'alt="([^"]+?) logo"', parts[k - 1])
        name = html.unescape(names[-1]) if names else slug
        for tr in re.finditer(r'<tr data-gpus="(\d*)"(.*?)</tr>', body, re.S):
            a = dict(re.findall(r'data-([a-z]+)="([^"]*)"', tr.group(0)[:500]))
            tds = re.findall(r"<td>(.*?)</td>", tr.group(0), re.S)
            cfg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", tds[0])).strip() if tds else ""
            badges = re.findall(r'<span class="badge[^"]*">([^<]+)</span>', tr.group(0))
            typ, term = billing(badges[0] if badges else "")
            price = float(a["pricegpu"]) if a.get("pricegpu") not in (None, "") else None
            form = "SXM" if "SXM" in cfg else ("PCIe" if "PCIe" in cfg.replace("PCIE", "PCIe") else ("NVL" if "NVL" in cfg else ""))
            rows.append({"date": TODAY, "gpu": gpu, "fournisseur": name, "slug": slug, "config": cfg[:80], "nb_gpu": int(a["gpus"]) if a.get("gpus") else None,
                         "format": form, "facturation": typ, "duree_mois": term, "prix": price,
                         "dispo": badges[-1].strip() if len(badges) > 1 else "", "europe": slug in EUROPE, "hyperscaler": slug in HYPERSCALERS})
    return rows


def listings_raw():
    """Pages tarifs publiques : sauvegarde brute, puis parsing des pages GetDeploying (H100, H200, B200)."""
    allrows = []
    for name, url in LIST_PAGES.items():
        h = get(url)
        if h:
            (D / "raw" / f"list_{name}.html").write_text(h)
            if name.startswith("getdeploying_"):
                allrows += parse_getdeploying(h, name.split("_")[1].upper())
        log("Prix affichés", name, "OK" if h else "échec", len(h or ""))
    if allrows:
        (D / "latest" / "listings.json").write_text(json.dumps({"date": TODAY, "rows": allrows}, ensure_ascii=False, indent=1))
        n = append_hist("listings", allrows, ["date", "gpu", "slug", "config", "facturation", "duree_mois"])
        log("Prix affichés :", len(allrows), "configurations ;", n, "lignes d'historique")


# ---------------------------------------------------------------- aicomputetracker (contrôle)
def tracker():
    j = get("https://aicomputetracker.com/forward-curves.json", as_json=True)
    if j:
        (D / "raw" / "aicomputetracker_forward_curves.json").write_text(json.dumps(j))
        log("aicomputetracker: asOf", j.get("asOf"))


if __name__ == "__main__":
    steps = sys.argv[1:] or ["kalshi", "ornn", "silicon", "fx", "news", "listings", "tracker", "archive"]
    raw = None
    for s in steps:
        try:
            if s == "kalshi":
                raw = kalshi()
                kalshi_backfill(raw)
            elif s == "ornn":
                ornn()
            elif s == "silicon":
                silicon()
            elif s == "fx":
                fx()
            elif s == "news":
                news()
            elif s == "tracker":
                tracker()
            elif s == "archive":
                archive_daily()
            elif s == "listings":
                listings_raw()
        except Exception as e:  # une source qui casse ne bloque pas les autres
            import traceback
            log("ÉCHEC", s, type(e).__name__, e)
            LOG.append(traceback.format_exc())
    (D / "log.txt").write_text(f"{dt.datetime.now(dt.timezone.utc).isoformat()}\n" + "\n".join(LOG) + "\n")
