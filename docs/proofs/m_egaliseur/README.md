# Protocole — Preuves Bot Égaliseur (8 tests)

> **Mode par défaut :** `operation_mode=test_only`. Tests T1–T4 : armer un sac (`POST /api/egaliseur/test/arm`) puis observer.

## Prérequis

```bash
docker compose up -d db bot backend egaliseur supervisor
# API : http://localhost:18000 (ou 8000 via port_beacon)
```

Script d'aide :

```bash
PYTHONPATH=bot:egaliseur python3 scripts/m_egaliseur_proof.py --check
PYTHONPATH=bot:egaliseur python3 scripts/m_egaliseur_proof.py --test 5
```

## Tests

| # | Objectif | Commande / action | Preuve attendue |
|---|---|---|---|
| T1 | Trailing posé | Sac réel `open` → Égaliseur actif | `m_egaliseur_T1_trailing.json` : `openOrders` avec `trailingDelta` |
| T2 | Fill trailing | Mouvement prix ou attente marché | sac `sold_auto`, PnL `myTrades` |
| T3 | Stop dur | Mark sous `hard_stop_price` | `sold_forced_stop`, ordre MARKET |
| T4 | Sortie temps | `max_hold_days=0.002` (~3 min) test documenté | `sold_forced_time` |
| T5 | Pas de BUY | `--test 5` | revue AST + grep |
| T6 | Pause + plafond | Pause API + scénario perte forcée | `egaliseur_actions`, alerte `daily_loss_cap` |
| T7 | Réconciliation | Sac `trailing_active` exclu puis inclus | superviseur `reconciliation_mismatch` puis OK |
| T8 | Indépendance | `docker stop ultiumgrid-bot` sac en trailing | egaliseur heartbeat + trailing actif |

Sorties : `docs/proofs/m_egaliseur/m_egaliseur_T*.json`
