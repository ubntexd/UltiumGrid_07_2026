# Progression — UltiumGrid_07_2026 (Spot v2)

Légende : `terminé` uniquement avec preuves.

| Étape | Statut | Preuve / notes |
|---|---|---|
| Migration Futures → Spot | **en cours** | `docs/migration_futures_to_spot.md`, `docs/spec.md` v2 |
| Audit public Spot | **terminé** | `docs/proofs/spot_public_audit.json` |
| Clés Spot Testnet | **bloqué** | clés Futures → `-2015` sur `testnet.binance.vision` |
| Module 1 Spot connecteur | **partiel** | code migré ; tests signés bloqués sans clés Spot |
| Modules 2–10 | adaptés partiellement | logique Spot ; intégration trading en attente clés |

## Blocage actif B-SPOT-KEYS

```
GET https://testnet.binance.vision/api/v3/account
→ HTTP 401 {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}
```

Les clés présentes (Futures Demo) ne sont **pas** valides sur Spot Testnet.

**Action :** créer des clés sur https://testnet.binance.vision → API Management, puis dans `.env` :

```
BINANCE_SPOT_TESTNET_API_KEY=...
BINANCE_SPOT_TESTNET_API_SECRET=...
BINANCE_SPOT_REST_BASE=https://testnet.binance.vision
BINANCE_SPOT_WS_BASE=wss://testnet.binance.vision/ws
```

Puis :

```bash
python scripts/diagnose_binance_orders.py
pytest bot/tests/test_m1_connector_integration.py -v -s
```

## Preuves publiques Spot (OK)

| Appel | Résultat |
|---|---|
| `GET /api/v3/ping` | 200 `{}` |
| `GET /api/v3/ticker/price?symbol=BTCUSDT` | 200 prix réel |
| `GET /api/v3/exchangeInfo` BTCUSDT | tickSize=0.01, stepSize=0.00001, minNotional=5 |
