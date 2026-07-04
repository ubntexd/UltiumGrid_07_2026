# Migration Futures → Spot (2026-07-04)

## Motif

Blocage persistant écriture ordres Futures Demo (`-1007` API + UI). Spot Testnet public répond normalement.

## Endpoints

| Concept | Futures (ancien) | Spot (nouveau) |
|---|---|---|
| REST | `demo-fapi.binance.com` / `testnet.binancefuture.com` | `https://testnet.binance.vision` |
| WS | `wss://demo-fstream.binance.com` | `wss://stream.testnet.binance.vision/ws` (prouvé ; `testnet.binance.vision/ws` = 404) |
| Compte | `/fapi/v2/account` | `/api/v3/account` |
| Ordre | `/fapi/v1/order` | `/api/v3/order` |
| openOrders / allOrders | `/fapi/v1/*` | `/api/v3/*` |
| exchangeInfo | `/fapi/v1/exchangeInfo` | `/api/v3/exchangeInfo` |
| Position | `/fapi/v2/positionRisk` | **soldes** `balances` |
| Funding / levier / marge | oui | **supprimés** |

## Fichiers modifiés

| Fichier | Action |
|---|---|
| `docs/spec.md` | Remplacé par spec Spot v2 ; ancien → `spec_v1_futures_deprecated.md` |
| `bot/ultiumgrid/connector/binance_spot.py` | **Nouveau** connecteur Spot |
| `bot/ultiumgrid/connector/binance_futures.py` | Conservé (déprécié, non importé) |
| `bot/ultiumgrid/connector/__init__.py` | Exporte `BinanceSpotClient` |
| `bot/ultiumgrid/engine/config.py` | Plus de `leverage` ; capital défaut 5000 ; `bags_capital_threshold_pct` |
| `bot/ultiumgrid/engine/grid.py` | Qty sans levier ; `real_position_qty` = balances ; close = SELL marché |
| `bot/ultiumgrid/bags/manager.py` | Réconciliation via `base_asset_qty` |
| `bot/ultiumgrid/guards/safety.py` | Panic = vente solde base libre |
| `bot/ultiumgrid/bot_runner.py` | Client Spot ; capital au lieu de marge |
| `backend/app/main.py` | Endpoints capital/marché sans funding |
| `frontend/app.js` | Bandeau capital ; plus de levier/funding |
| `scripts/diagnose_binance_orders.py` | À réécrire pour Spot |
| `bot/tests/*` | Imports Spot ; SymbolFilters + base/quote |
| `.env.example` | `BINANCE_SPOT_*` + URLs vision |

## Tests à ré-exécuter (Spot)

| Test | Statut |
|---|---|
| Public ping/ticker/exchangeInfo | **prouvé** `docs/proofs/spot_public_audit.json` |
| `GET /api/v3/account` signé | **bloqué** : clés Futures → `-2015` sur Spot |
| place/cancel ordre | **bloqué** : clés Spot manquantes |
| unit anti-doublon / retry_exhausted / grille | à rejouer après migration imports |

## Clés API

Les clés Futures Demo **ne fonctionnent pas** sur `testnet.binance.vision` (preuve : HTTP 401 `-2015`).

**Action requise :** générer des clés sur https://testnet.binance.vision → API Management, puis :

```
BINANCE_SPOT_TESTNET_API_KEY=...
BINANCE_SPOT_TESTNET_API_SECRET=...
BINANCE_SPOT_REST_BASE=https://testnet.binance.vision
BINANCE_SPOT_WS_BASE=wss://stream.testnet.binance.vision/ws
```
