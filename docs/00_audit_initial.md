# Audit initial — UltiumGrid_07_2026

Date UTC : `2026-07-04T08:25:45+00:00`  
Preuves brutes : `docs/proofs/00_binance_audit_raw.json`  
Règle appliquée : zéro affirmation sans preuve d’exécution jointe.

---

## 1. Environnement VPS constaté

| Élément | Valeur constatée | Preuve |
|---|---|---|
| OS | Ubuntu 24.04.4 LTS (Noble Numbat), kernel `6.8.0-117-generic` | `uname -a`, `/etc/os-release` |
| Hostname | `a976765959.local` | `uname -a` |
| IP publique VPS | `176.97.70.254` | `GET https://api.ipify.org` → `176.97.70.254` |
| Python | `3.12.3` (`/usr/bin/python3`) | `python3 --version` |
| Node.js | `v22.23.1` | `node --version` |
| npm | `10.9.8` | `npm --version` |
| Docker | `29.6.1` (build `8900f1d`) | `docker --version` |
| Docker Compose | `v5.3.0` | `docker compose version` |
| psql client | non installé | `psql --version` → « Please ask your administrator » / not installed |
| Conteneurs actifs | aucun | `docker ps` → liste vide |

### Dépendances Python du projet (`.venv`)

Installées via le kit de démarrage (sortie `pip list` du venv) :

- `requests 2.34.2`
- `python-dotenv 1.2.2`
- `eth-account 0.13.7` (+ dépendances Hyperliquid)
- pas de FastAPI, SQLAlchemy, websockets, pytest, etc.

---

## 2. Structure du dépôt (état réel)

Fichiers hors `.git` / `.venv` :

```
./.env                  (présent, non suivi git — OK)
./.env.example          (suivi git — PROBLÈME : contenait des clés réelles)
./.gitignore
./README.md
./requirements.txt
./scripts/connect_all_testnets.py
./scripts/connect_binance_futures_testnet.py
./scripts/connect_hyperliquid_testnet.py
```

Absent au moment de l’audit :

- `docs/spec.md` (cahier des charges)
- `Dockerfile` / `docker-compose.yml`
- code métier (bot, backend, frontend, DB)
- tests d’intégration
- `docs/progression.md`, `docs/audit_modules.md`, etc. (créés par cet audit)

Branche git : `main`, à jour avec `origin/main`.  
Derniers commits :

- `d2f2225` phase 0: kit de demarrage
- `88fc338` Add Binance Futures and Hyperliquid testnet connection scripts
- `7025acf` Initial commit: UltiumGrid_07_2026

---

## 3. Clés API — présence (valeurs jamais affichées)

| Variable | Présente dans `.env` | Longueur | Notes |
|---|---|---|---|
| `BINANCE_FUTURES_TESTNET_API_KEY` | oui | 64 | alphanumérique, sans espace |
| `BINANCE_FUTURES_TESTNET_API_SECRET` | oui | 64 | alphanumérique, sans espace |
| `BINANCE_FUTURES_TESTNET_SECRET_KEY` | non (dans `.env`) | — | nom alternatif présent uniquement dans l’ancien `.env.example` |
| `HL_TESTNET_PRIVATE_KEY` | non (vide) | 0 | hors scope immédiat |
| `HL_TESTNET_ACCOUNT_ADDRESS` | non (vide) | 0 | hors scope immédiat |

Incohérence de nommage constatée avant correction :

- Script `scripts/connect_binance_futures_testnet.py` lit `BINANCE_FUTURES_TESTNET_API_SECRET`
- Ancien `.env.example` exposait `BINANCE_FUTURES_TESTNET_SECRET_KEY`
- Les deux secrets (env / example) étaient **identiques** (égalité vérifiée sans affichage)

**Sécurité :** l’ancien `.env.example` committait des clés réelles dans l’historique git. Ces clés doivent être considérées comme compromises et régénérées côté Binance, même après nettoyage du fichier.

Permissions Futures / trading : **non vérifiable** — aucun endpoint authentifié n’accepte les clés actuelles (voir §4).

---

## 4. Connectivité Binance Futures Testnet — appels réels

Base URL utilisée : `https://testnet.binancefuture.com`  
Dump complet : `docs/proofs/00_binance_audit_raw.json`

### 4.1 Endpoints publics — OK

#### `GET /fapi/v1/ping`

```
HTTP 200
Body: {}
```

#### `GET /fapi/v1/time`

```
HTTP 200
Body: {"serverTime":1783153545513}
```

#### `GET /fapi/v1/ticker/price?symbol=BTCUSDT`

```
HTTP 200
Body: {"price":"62504.20","symbol":"BTCUSDT","time":1783153538126}
```

#### `GET /fapi/v1/premiumIndex?symbol=BTCUSDT`

```
HTTP 200
Body: {"symbol":"BTCUSDT","markPrice":"62523.40000000","indexPrice":"62563.42826087","estimatedSettlePrice":"62545.54593478","lastFundingRate":"0.00010000","interestRate":"0.00010000","nextFundingTime":1783180800000,"time":1783153545001}
```

#### `GET /fapi/v1/exchangeInfo` (extrait BTCUSDT)

```
HTTP 200
symbols_count=723
BTCUSDT status=TRADING
pricePrecision=2 quantityPrecision=4
PRICE_FILTER tickSize=0.10
LOT_SIZE stepSize=0.0001 minQty=0.0001
MIN_NOTIONAL notional=50
```

### 4.2 Endpoints authentifiés — ÉCHEC

#### `GET /fapi/v2/account` (signé HMAC-SHA256, clés du `.env`)

```
HTTP 401
Body: {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action"}
```

#### `POST /fapi/v1/listenKey` (header `X-MBX-APIKEY` seul)

```
HTTP 401
Body: {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action"}
```

Également testé sans succès :

- même paire de clés avec `recvWindow=60000`
- base alternative `https://demo-fapi.binance.com` → même `401 / -2015`
- secret lu sous le nom `BINANCE_FUTURES_TESTNET_SECRET_KEY` (identique au secret `.env`)

Script existant :

```
.venv/bin/python scripts/connect_binance_futures_testnet.py
→ Ping OK, Time OK, Symbols OK
→ ERREUR auth API : 401 ... {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action"}
EXIT=1
```

### 4.3 Interprétation factuelle (sans spéculation sur la cause exacte côté Binance)

Constat mesurable uniquement :

1. Le réseau VPS → `testnet.binancefuture.com` fonctionne (endpoints publics HTTP 200).
2. Les clés présentes dans `.env` sont rejetées pour toute action authentifiée (`-2015`).
3. Impossible de lire le solde, les positions, les ordres, ou de placer un ordre.
4. Impossible de confirmer `canTrade`, levier, ou permissions Futures.

Causes possibles côté Binance (non discriminées faute d’accès compte) : clé invalide/expirée, restriction IP ne listant pas `176.97.70.254`, permissions insuffisantes, ou clés créées sur un autre environnement.

---

## 5. Cahier des charges

Recherche de `cahier_des_charges_grid_bot.md` / `docs/spec.md` dans le dépôt et sous `/home/dev` : **fichier introuvable**.

Sans `docs/spec.md`, les modules métier ne peuvent pas être audités ligne à ligne contre la source de vérité fonctionnelle.

Le prompt de développement liste des exigences (cycle +15/+10, coupe paliers 10/14, sacs, garde-fous, UI) mais ce n’est pas un substitut au cahier des charges complet demandé.

---

## 6. Infrastructure Docker

| Exigence | État |
|---|---|
| Dockerfile par service | absent |
| `docker-compose.yml` | absent |
| Volumes DB persistants | absent |
| Test `docker compose down -v && up --build` | **non vérifié** (rien à lancer) |

Docker moteur et Compose sont installés et opérationnels (versions ci-dessus), mais aucun service projet n’est containerisé.

---

## 7. Verdict de l’audit initial

| Critère de passage vers Module 1 | Résultat |
|---|---|
| Connectivité publique VPS ↔ Binance Testnet | **OK** (preuves §4.1) |
| Authentification compte Futures Testnet | **ÉCHEC** (preuves §4.2) |
| Permissions trading vérifiées | **non vérifié** (bloqué par §4.2) |
| `docs/spec.md` présent | **absent** |
| Docker projet prêt | **absent** |

**Blocage réel :** les modules 1 à 10 exigent des opérations authentifiées (ordres, positions, solde) et une spécification fonctionnelle. Aucun code métier supplémentaire ne sera écrit tant que :

1. de nouvelles clés API Futures Testnet valides acceptent `GET /fapi/v2/account` depuis l’IP `176.97.70.254`, et
2. le fichier `docs/spec.md` (cahier des charges) est fourni.

Voir `docs/progression.md`.
