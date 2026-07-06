# Protocole — Instance SOL/USDT (comparaison parallèle BTC v2)

## Objectif

Run organique **SOL/USDT** sur compte Demo **séparé**, paramètres identiques au BTC v2 (sauf capital 4000 USDT et symbole), **aucune interférence** avec l'instance BTC.

## Stack isolée

| Élément | Instance BTC | Instance SOL |
|---|---|---|
| Compose | `docker-compose.yml` | `docker-compose.sol.yml` |
| Projet Docker | `ultiumgrid_07_2026` (défaut) | `ultiumgrid_sol` |
| Env | `.env` | `.env.sol` |
| DB | `ultiumgrid` / volume `pgdata` | `ultiumgrid_sol` / `pgdata_sol` |
| Réseau | `ultiumnet` | `ultiumnet_sol` |
| API docker | `127.0.0.1:18000` | `127.0.0.1:18100` |
| UI docker | `127.0.0.1:18080` | `127.0.0.1:18180` |
| Beacon Cursor | `:8080` / `:8000` | `:8081` / `:8001` |
| UI label | UltiumGrid — Instance BTC (bleu) | UltiumGrid — Instance SOL (violet) |
| Égaliseur | `test_only` | `test_only` |

## Configuration SOL

| Paramètre | Valeur |
|---|---|
| Symbole | SOLUSDT |
| Capital | 4000 USDT |
| Paliers | 20 |
| Pas | 0,40 % |
| BNB discount | true (appliqué au prochain cycle) |
| Seuil cycle | **+5 USD brut** *(appliqué **2026-07-05 22:40 UTC** via `close_now` — cycle 6+ ; cycle 5 clos `config_change`)* |

Précheck : `docs/proofs/m_sol_instance_precheck.json` (minNotional 100 USDT/palier).

### Viabilité SOL (seuil 5 USD, BNB discount)

| Indicateur | Valeur (Module 7bis) |
|---|---|
| `net_per_grid` | 0,50 USDT |
| `grids_to_cycle` | 10 |
| `fees_initial_inventory` | 1,50 USDT |
| **`net_at_gross_threshold`** | **3,50 USDT** |

> Comparaison : avec seuil +15 USD et BNB, `net_at_gross_threshold` = 13,50 USDT (30 grilles). Le coût fixe d'inventaire initial pèse proportionnellement plus sur des cycles plus fréquents.

## Incohérence d'affichage UI (résolue)

**Cause confirmée (pas cache navigateur) :** au premier lancement, `POST /api/config` SOL a été **rejeté** (`bnb_fee_discount=true` alors que BNB=0). Le script de lancement n'a pas vérifié le HTTP 400 ; le bot a démarré avec la **config par défaut BTCUSDT** (5000 USDT, pas 0,25 %). L'UI SOL (`:18180`) lit `GET /api/running` sur le backend SOL (`:18100`) — elle affichait donc des **données BTC réelles** servies par le backend, pas une erreur réseau ni un cache.

**Preuve :** `docs/proofs/m3_sol_instance_v1/pre_launch_proof.json` → `post_start.symbol = BTCUSDT`.

**Résolution :** relance avec config SOLUSDT valide + reset état DB (2026-07-05 ~22:02 UTC).

## Isolation BTC ↔ SOL

Test croisé backend (2026-07-05 22:23 UTC) :

1. `stop backend` BTC → API SOL `:18100` OK, API BTC `:18000` down ✅
2. `stop backend` SOL → API BTC OK, API SOL down ✅

Preuve : `docs/proofs/m_sol_instance_isolation_check.json` (également `docs/proofs/m_sol_isolation_proof.json` pour DB/containers).

## Fenêtres de comparaison

| Instance | Début | Fin cible |
|---|---|---|
| BTC v2 | 2026-07-05 19:23 UTC | 2026-07-06 19:23 UTC |
| SOL v1 | *heure réelle au lancement* | alignée si possible |

**Règle :** ne jamais comparer des durées inégales sans le signaler dans le rapport.

## Déploiement (clés requises)

```bash
cp .env.sol.example .env.sol
# Éditer .env.sol — clés Demo SOL uniquement

python3 scripts/m_sol_instance_precheck.py

docker compose -p ultiumgrid_sol -f docker-compose.sol.yml --env-file .env.sol up -d --build

# Beacon (optionnel, accès Cursor)
nohup python3 scripts/port_beacon_sol.py > /tmp/port_beacon_sol.log 2>&1 &

python3 scripts/m_sol_isolation_proof.py

python3 scripts/m3_sol_instance_launch.py

python3 scripts/m3_organic_long_run.py \
  --api http://127.0.0.1:18100 \
  --out-dir docs/proofs/m3_sol_instance_v1 \
  --compose-project ultiumgrid_sol \
  --compose-file docker-compose.sol.yml \
  --db-name ultiumgrid_sol \
  --duration-h 24
```

## URLs UI

- Direct VPS : http://127.0.0.1:18180/
- Via beacon : http://localhost:8081/
- Égaliseur : http://127.0.0.1:18180/egaliseur.html

## Rapport comparatif (à produire)

Métriques côte à côte BTC vs SOL : fills, round-trips, Grid Profit, Floating, recentrage Cas A/B, garde-fous.

## Règle run

Aucune intervention manuelle pendant le run, sauf garde-fou ou alerte critique réelle.
