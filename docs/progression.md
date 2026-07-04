# Progression — UltiumGrid_07_2026 (Spot Demo)

| Étape | Statut | Preuve |
|---|---|---|
| Migration Futures → Spot | **terminée** (URL corrigée) | `docs/migration_futures_to_spot.md` |
| Module 1 — Connecteur Spot | **terminé** | place/cancel réel OK, 10 tests M1 passés |
| Module 2 — DB | **terminé** | `m2_database_sql.json` |
| Modules 3–10 | en cours | logique adaptée Spot |

## Correction URL (2026-07-04)

Bug : le code pointait vers `https://testnet.binance.vision` alors que les clés `demo.binance.com` fonctionnent sur **`https://demo-api.binance.com`**.

Preuve :
- `testnet.binance.vision` + clés demo → `-2015`
- `demo-api.binance.com` + mêmes clés → account 200, **place/cancel ordre 200**

Preuves : `docs/proofs/spot_url_fix.json`, `docs/proofs/m1_place_cancel_order.json`, `docs/proofs/spot_order_diagnosis.json`
