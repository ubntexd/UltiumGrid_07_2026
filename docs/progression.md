# Progression — UltiumGrid_07_2026 (Spot Demo)

| Étape | Statut | Preuve |
|---|---|---|
| Migration Futures → Spot | **terminée** | `migration_futures_to_spot.md` |
| Module 1 — Connecteur | **terminée** | place/cancel, WS, anti-doublon |
| Module 2 — DB | **terminée** | SQL direct |
| Module 3 — Grille | **terminée** | `m3_grid_integration.json` |
| Module 4 — Coupe | **terminée** | `m4_cut_incomplete_spot.json` |
| Module 5 — Sacs | **terminée** | `m5_bag_sell_spot.json` |
| Module 6 — Panic | **terminée** | `m6_panic_spot.json` |
| Module 7 — API | **terminée** | `m7_api_crosscheck.json` |
| Module 7bis — Config + viabilité | **terminée** | `m7bis_config_spot.json` |
| Module 7ter — Marché | **terminée** | `/api/market` live, sélecteur symbol |
| Module 8 — UI audit | **terminée** | `m8_audit_ui.json` |
| Module 9 — Reprise crash | **terminée** | `m9_crash_recovery.json` |
| Module 10 — Audit final | **terminée** | `audit_final.md` |
| Docker | **terminée** | `docker compose up --build` |

## Lancer

```bash
cp .env.example .env   # clés demo.binance.com
# BINANCE_SPOT_REST_BASE=https://demo-api.binance.com
# BINANCE_SPOT_WS_BASE=wss://demo-stream.binance.com/ws
docker compose up --build -d
```

UI : http://localhost:8080/ — API : http://localhost:8000/
