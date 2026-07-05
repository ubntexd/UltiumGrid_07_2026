# Protocole — Run organique Module 3 (24-48h)

## Objectif

Fermer le Module 3 à 100 % **sans raccourci** :
- Trigger cycle **+15 USD brut** (`close_reason=trigger_15`) en conditions marché réelles
- Cas A production : `idle_recenter_no_fill` avec `idle_recenter_min=20`
- Cas B production : `forced_sell_stuck_level` avec `stuck_sell_min=15`

## Prérequis

- `docker compose up -d` — 5 services dont **supervisor**
- Bot **running** avec config production (`cycle_trigger_usd=15`, `idle_recenter_min=20`, `stuck_sell_min=15`)
- **Aucune intervention manuelle** pendant la durée du run

## Lancement

```bash
cd /home/dev/dev/UltiumGrid_07_2026
nohup python3 scripts/m3_organic_long_run.py --duration-h 48 --interval-s 300 \
  >> docs/proofs/m3_organic_long_run/runner.log 2>&1 &
echo $! > docs/proofs/m3_organic_long_run/runner.pid
```

Reprise après coupure :

```bash
python3 scripts/m3_organic_long_run.py --resume --duration-h 48 --interval-s 300
```

## Artefacts

| Fichier | Contenu |
|---|---|
| `manifest.json` | Début/fin, statut, événements détectés |
| `snapshots/*.json` | État API + DB toutes les 5 min |
| `runner.log` | Sortie stdout du moniteur |
| `summary.json` | Synthèse à la fin des 48h |

## Critères de succès Module 3

| Critère | Preuve attendue |
|---|---|
| Trigger +15 organique | `cycles.close_reason='trigger_15'` + `gross_pnl >= 15` dans snapshot |
| Cas A (si marché OOR 20+ min sans fill) | `order_attempts.purpose=idle_recenter_no_fill` + nouveau cycle |
| Cas B (si SELL bloqué 15+ min) | `order_attempts.outcome=forced_sell_stuck_level` |
| Superviseur actif | `supervisor_http_ok=true`, pas d'alerte `bot_unresponsive` non résolue |
| Pas de raccourci | `cycle_trigger_usd` reste 15 sur tous les snapshots |

## Module recentrage (pré-requis)

Preuve implémentation + config production : `docs/proofs/m3_recenter_cas_ab_module.json`  
Preuves intégration (timers documentés 0.05 min) : `m3_open_sequence_clarifications.json`
