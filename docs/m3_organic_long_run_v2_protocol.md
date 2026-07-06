# Protocole — Run organique Module 3 v2 (24h, config cible 0,40 % + BNB)

> Run **distinct** du run annulé (`docs/proofs/m3_organic_long_run/`).  
> Justification paramètres : `docs/proofs/m7bis_target_config_simulation.json`

## Feu vert

**Date :** 2026-07-05 (UTC) — feu vert explicite utilisateur pour ce lancement précis.  
Gel précédent levé uniquement pour ce run documenté.

## Configuration exacte

| Paramètre | Valeur |
|---|---|
| `symbol` | BTCUSDT |
| `capital_usdt` | 5000 |
| `num_levels` | 20 |
| `step_pct` | **0,40 %** |
| `cycle_trigger_usd` | 15 |
| `bnb_fee_discount` | **true** (bloquant si `bnb_free ≤ 0`) |
| `idle_recenter_min` | 20 |
| `stuck_sell_min` | 15 |
| `hard_stop_pct` | -8 % |
| `daily_circuit_breaker_usd` | -40 |

Simulation viabilité : ~24 grilles/cycle (vs 120 en 0,25 % sans BNB) — `m7bis_target_config_simulation.json`.

## Durée cible

**24 heures** (première validation ; extension possible sur demande explicite).

## Prérequis validés avant Start

- Module orphelin : conforme 5/5 — `m3_orphan_position_proof.json`
- Compte : 0 BTC résiduel, ~5000 USDT, BNB > 0
- 0 cycle `open` en base
- Config appliquée confirmée via `GET /api/config`

## Lancement

```bash
cd /home/dev/dev/UltiumGrid_07_2026
# Preuve + Start (une fois)
python3 scripts/m3_organic_long_run_v2_launch.py

# Moniteur 24h (arrière-plan)
nohup python3 scripts/m3_organic_long_run.py \
  --out-dir docs/proofs/m3_organic_long_run_v2 \
  --duration-h 24 --interval-s 300 --api http://127.0.0.1:8000 \
  >> docs/proofs/m3_organic_long_run_v2/runner.log 2>&1 &
echo $! > docs/proofs/m3_organic_long_run_v2/runner.pid
```

## Artefacts

| Fichier | Contenu |
|---|---|
| `pre_launch_proof.json` | État compte + config avant Start |
| `manifest.json` | Début/fin, événements, config gelée |
| `snapshots/*.json` | État toutes les 5 min |
| `summary.json` | Synthèse finale |

## Critères de clôture (détection automatique manifest)

| Critère | Preuve |
|---|---|
| `trigger_15` organique | `close_reason=trigger_15`, Grid Profit corrigé |
| Cas A ou B recentrage | `order_attempts` si marché le justifie |
| Pas d'alerte orpheline / garde-fou / réconciliation | kinds actifs vides dans snapshots |
| Config inchangée | `step_pct=0.4`, `cycle_trigger_usd=15` sur tous snapshots |

## Règle d'intervention

**Aucune intervention manuelle** pendant 24h (pas Start/Stop/Panic/Config), sauf garde-fou légitime ou alerte critique superviseur nécessitant action réelle.

## Mécanismes actifs attendus

- Achat initial marché à l'ouverture
- Recentrage Cas A (20 min) / Cas B (15 min)
- Coupe progressive paliers 10/14
- Garde-fous stop dur / circuit breaker
- Superviseur 10bis + surveillance position orpheline
- PnL flottant recalculé à chaque tick WS
