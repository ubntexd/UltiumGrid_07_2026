# Progression — UltiumGrid_07_2026 (Spot Demo)

| Étape | Statut | Preuve |
|---|---|---|
| Migration Futures → Spot | **terminée** | `migration_futures_to_spot.md` |
| Module 1 — Connecteur | **terminée** | place/cancel, WS, anti-doublon |
| Module 2 — DB | **terminée** | SQL direct |
| Module 3 — Grille | **terminée** | `m3_grid_integration.json` + §2bis unit `test_m3_idle_recenter_unit.py` |
| Module 3bis — Recentrage hors fourchette | **terminée (unit)** | idle_recenter + stuck_sell code ; unit idle ; stuck_sell **non vérifié** en integration live |
| Module 4 — Coupe | **terminée** | `m4_cut_incomplete_spot.json` |
| Module 5 — Sacs | **terminée** | `m5_bag_sell_spot.json` |
| Module 6 — Panic | **terminée** | `m6_panic_spot.json` |
| Module 7 — API | **terminée** | `m7_api_crosscheck.json` |
| Module 7bis — Config + viabilité | **terminée** | `m7bis_config_spot.json` |
| Module 7ter — Marché | **terminée** | `/api/market` live, sélecteur symbol |
| Module 7quater — Fees réels | **terminée** | `m7quater_fees_mytrades.json`, `/api/fees`, onglet Fees |
| Module 8 — UI dynamique + graphiques | **terminée** | `m8_audit_ui.json`, `m8_charts.json` |
| Module 9 — Reprise crash | **re-validée** | `m9_crash_recovery.json` + anti-doublon cycles (`audit_ui_bugfix.md` §6, restart prouve 1 seul `open`) |
| Module 10bis — Superviseur | **terminée** | container séparé, preuves `m10bis_*.json` |
| Module 10 — Audit final | **terminée** | `audit_final.md` |
| Docker (5 services) | **terminée** | bot, backend, frontend, supervisor, db |

## Lancer

```bash
cp .env.example .env   # clés demo.binance.com
# BINANCE_SPOT_REST_BASE=https://demo-api.binance.com
# BINANCE_SPOT_WS_BASE=wss://demo-stream.binance.com/ws
docker compose up --build -d
```

UI : http://localhost:8080/ — API : http://localhost:8000/
