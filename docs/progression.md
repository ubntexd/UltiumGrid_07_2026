# Progression — UltiumGrid_07_2026 (Spot Demo)

| Étape | Statut | Preuve |
|---|---|---|
| Migration Futures → Spot | **terminée** | `docs/migration_futures_to_spot.md` |
| URL `demo-api.binance.com` | **terminée** | `docs/proofs/spot_url_fix.json` |
| Module 1 — Connecteur Spot | **terminée** | place/cancel, 10 tests M1 |
| Module 2 — DB | **terminée** | `m2_database_sql.json` |
| Module 3 — Grille | **terminée** | `m3_grid_integration.json` (BUY placés, SELL pending sans BTC, croisement Binance↔DB) |
| Module 4 — Coupe | **terminée** | `m4_cut_incomplete_spot.json` (qty réelle + incomplete) |
| Module 5 — Sacs | **terminée** | `m5_bag_sell_spot.json` (vente marché, SQL closed) |
| Module 6 — Panic | **terminée** | `m6_panic_spot.json` (vente réelle + annulation ordres) |
| Module 7 — API | **terminée** (lecture + start/stop live Docker) | `/api/running`, `/api/capital`, start → 10 BUY |
| Module 7bis–10 | partiel | config/UI présents ; audit UI détaillé à compléter |

## Preuves clés Spot

- Place/cancel : `docs/proofs/m1_place_cancel_order.json`
- Grille 4 niveaux : `docs/proofs/m3_grid_integration.json`
- Coupe + sac + panic : `m4_*.json`, `m5_*.json`, `m6_*.json`
- Docker live : start bot → 10 ordres BUY success (logs bot), capital USDT libre lu via API

## Comportement Spot noté

- Au démarrage sans BTC : seuls les **BUY** sont placés ; les **SELL** restent `pending` jusqu’à fill.
- `GET /api/v3/order` peut renvoyer `-2013` sur demo alors que `openOrders` liste l’ordre — vérif via `openOrders`.
