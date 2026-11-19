# compute-data

Collecte quotidienne des prix du compute GPU pour l'écran **Compute Group — Run GPU**.

- `fetch.py` : Kalshi (API publique, séries `KX<GPU>MS`), Ornn OCPI, Silicon Data, EUR/USD, aicomputetracker.
- `build.py` : construit `dash/*.json` lus par le dashboard.
- `.github/workflows/fetch.yml` : toutes les 4 heures, et à chaque modification du code.
- `data/history/*.csv` : historique quotidien, jamais écrasé (Silicon Data : les 30 jours de chaque page sont fusionnés à chaque passage).
- `data/history/kalshi_snapshots.csv` : tous les relevés Kalshi ; `kalshi_forwards.csv` garde par jour le relevé le plus coté.
- `data/archive/AAAA-MM/JJ/` : relevés bruts compressés (Kalshi à chaque passage, pages Ornn et Silicon Data une fois par jour).
- `data/log.txt` : journal du dernier passage.

## Site autonome (Cloudflare Pages, accès protégé)

`site/` contient l'écran Run GPU en version autonome (même page que le dashboard Claude, sans le journal des cotations OTC, qui reste privé dans Claude). `site/shim.js` remplace le moteur du dashboard ; `build_site.py` copie `dash/` dans `site/data/`. Le workflow déploie `site/` sur Cloudflare Pages (projet `compute-desk`) à chaque passage, dès que les secrets `CLOUDFLARE_API_TOKEN` et `CLOUDFLARE_ACCOUNT_ID` existent.

Mise en place (une fois) :
1. Créer un compte Cloudflare, puis un jeton d'API avec la permission « Cloudflare Pages : Edit » (My Profile → API Tokens).
2. Dans GitHub → Settings → Secrets and variables → Actions, ajouter `CLOUDFLARE_API_TOKEN` (le jeton) et `CLOUDFLARE_ACCOUNT_ID` (visible dans le tableau de bord Cloudflare).
3. Lancer le workflow (Actions → fetch → Run workflow). Le site sort sur `https://compute-desk.pages.dev`.
4. Protéger l'accès : Cloudflare → Workers & Pages → compute-desk → Settings → « Enable access policy », puis dans Zero Trust → Access, autoriser les e-mails voulus (gratuit jusqu'à 50 utilisateurs). Faire de même pour les URL de prévisualisation.
5. Passer ce repo en privé (Settings → General → Danger Zone → Change visibility).

## Indice des prix affichés : fiabilité (méthode v2)

- H100 : PCIe et NVL exclus de l'indice principal, PCIe suivi à part ; H200 : NVL exclu.
- Plateformes serverless exclues ; hyperscalers dans une colonne à part.
- Deux lignes : tous les prix et prix disponibles (hors rupture et précommande).
- Prix hors bornes exclus. Contrôles à chaque relevé : nombre de lignes, nombre de fournisseurs, variation de plus de 15 %, recoupement avec les pages officielles (Lambda, Nebius, RunPod, Verda, Scaleway). Un contrôle en échec marque le jour SUSPECT, avec le motif.
- Toute modification de méthode est datée dans `data/static/methode.json` (publiée dans `dash/methode.json`).
