# Progression — UltiumGrid_07_2026 (Spot Demo)

> **Source de vérité suivi projet :** `docs/journal_de_bord.md` — ce fichier détaille l'état technique des modules.

| Étape | Statut | Preuve |
|---|---|---|
| Migration Futures → Spot | **terminée** | `migration_futures_to_spot.md` |
| Module 1 — Connecteur | **terminée** | place/cancel, WS, anti-doublon |
| Module 2 — DB | **terminée** | SQL direct |
| Module 3 — Grille | **validé logique + intégration courte ; run organique complet non validé** | voir § Module 3 ci-dessous |
| Module 3bis — Recentrage hors fourchette | **terminée (code)** ; Cas A/B organique prod **non observé** | `m3_recenter_cas_ab_module.json` |
| Module 4 — Coupe | **terminée** | `m4_cut_incomplete_spot.json` |
| Module 5 — Sacs | **terminée** | `m5_bag_sell_spot.json` |
| Module 6 — Panic | **terminée** | `m6_panic_spot.json` |
| Module 7 — API | **terminée** | `m7_api_crosscheck.json` |
| Module 7bis — Config + viabilité | **terminée** ; nouveau paramétrage 0,40%+BNB **préparé, non lancé** | `m7bis_target_config_simulation.json` |
| Module 7ter — Marché | **terminée** | `/api/market` live, sélecteur symbol |
| Module 7quater — Fees réels | **terminée** | `m7quater_fees_mytrades.json`, `/api/fees`, onglet Fees |
| Module 8 — UI dynamique + graphiques | **terminée** | `m8_audit_ui.json`, `m8_charts.json` |
| Module 9 — Reprise crash | **re-validée** | `m9_crash_recovery.json` |
| Module 10bis — Superviseur | **terminée** | container séparé, preuves `m10bis_*.json` |
| Module 10 — Audit final | **en attente** | nouveau run après feu vert utilisateur |

## Module 3 — Run organique 48h **ANNULÉ** (2026-07-05)

**Statut manifest** : `cancelled_by_user` — `docs/proofs/m3_organic_long_run/manifest.json`  
**Durée effective** : ~25h (démarré 2026-07-04 16:57 UTC, arrêt 2026-07-05 18:15 UTC)  
**Raison** : config actuelle (~120 grilles/cycle, ~1 cycle/6-7 jours) incompatible avec l'objectif de micro-gains fréquents.

### Acquis malgré l'annulation (preuves réelles >24h)

| Validation | Preuve |
|---|---|
| Grid Profit par appariement round-trip (bug trouvé et corrigé en cours de run) | `m3_grid_profit_correction_cycle3.json` |
| Symétrie BUY initial / SELL inventaire (bug trouvé et corrigé) | `m3_matched_trades_symmetry_correction.json` |
| Connecteur + reconciliation + superviseur sans crash >24h | `m3_organic_long_run/` (303 snapshots) |
| Rétroactivité Grid Profit cycle 2 | `m3_grid_profit_retroactive_corrections.json` |

### Non validé (reste ouvert)

| Objectif | État |
|---|---|
| Recentrage Cas A/B organique (seuils prod 20/15 min) | Jamais observé en conditions réelles |
| Trigger +15 organique (`close_reason=trigger_15`) | Jamais atteint (cohérent avec ~120 grilles) |
| Garde-fous sur tendance prolongée (hard stop, circuit breaker) | Non déclenchés |

**Clôture cycle 3** : `user_stop` (pas Panic) — `cycle3_final_state.json`  
Position résiduelle conservée : ~0,051 BTC.

## Prochain paramétrage cible (préparé, pas lancé)

| Paramètre | Actuel (annulé) | Cible |
|---|---|---|
| Pas grille | 0,25 % | **0,40 %** |
| Frais BNB | off | **on (obligatoire au démarrage)** |
| Seuil cycle | +15 USD | +15 USD (inchangé) |

Simulation viabilité : `docs/proofs/m7bis_target_config_simulation.json`  
**Bloquant** : `bnb_free = 0` sur compte Demo — approvisionnement requis avant tout nouveau run.

**Gel** : pas de nouveau run longue durée sans feu vert explicite utilisateur.

| Docker (5 services) | **terminée** | `docker_cold_start.json` |

## Lancer

```bash
cp .env.example .env
docker compose up --build -d
```

UI : http://127.0.0.1:18080/ — API : http://127.0.0.1:18000/
