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
| **Surveillance position orpheline** (post-Stop + Start sécurisé) | **conforme — 5/5 tests** | `m3_orphan_position_proof.json`, `m3_orphan_C2_blocked.json` — voir § ci-dessous |
| Journal de trades + traçabilité sacs | **terminée** | `m_journal_bags_live_proof.json`, `m_bags_traceability.json` |
| **Bot Égaliseur (Bot 2)** | **en cours — code livré, 8 tests réels non validés** | page `/egaliseur.html`, `operation_mode=test_only` pendant run v2 |
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
Position résiduelle conservée au Stop : ~0,051 BTC (liquidée ensuite lors des tests orphelin — voir § micro-cycles).

## Surveillance position orpheline — **conforme** (2026-07-05)

Module bloquant levé avant tout nouveau run longue durée. Implémentation : superviseur `orphan_position_unwatched`, `residual_position_warning` au Stop, `entry_avg` sécurisé au Start (`prior_bot_state_entry_avg` / `myTrades_fifo`).

| Test | Description | Statut | Preuve |
|---|---|---|---|
| **A1** | Alerte orpheline après Stop + délai | **passé** | `m3_orphan_position_proof.json` → `tests.A1` |
| **A2** | Pas d'alerte si position nulle / sous seuil | **passé** | `m3_orphan_position_proof.json` → `tests.A2` |
| **B1** | `residual_position_warning` dans réponse Stop + `last_command` | **passé** | `m3_orphan_position_proof.json` → `tests.B1` |
| **C1** | Start stock existant → `prior_bot_state_entry_avg` (pas `center_price`) | **passé** | `m3_orphan_position_proof.json` → `tests.C1` |
| **C2** | Start sans historique → blocage `untracked_inventory` | **passé** | `m3_orphan_C2_blocked.json` |

Script reproductible : `ORPHAN_STOPPED_MIN_S=0 PYTHONPATH=bot python3 scripts/m3_orphan_position_proof.py`  
Tests unitaires : `bot/tests/test_m3_orphan_position.py`  
Documentation : `docs/audit_modules.md` (section dédiée), `docs/spec.md` §5bis.

## Micro-cycles de test développement (2026-07-05, 18:49–18:52 UTC)

**Contexte :** exécution de `scripts/m3_orphan_position_proof.py` pour valider le module orphelin (tests A1, A2, B1, C1, C2). **Ce n'était pas un run de production** ni le paramétrage cible 0,40 % + BNB.

| Paramètre | Valeur (config isolée de test) |
|---|---|
| `capital_usdt` | 500 |
| `num_levels` | 4 |
| `step_pct` | 0,3 % |
| `bnb_fee_discount` | false |

| Cycle | Ouvert (UTC) | Clôturé (UTC) | `close_reason` | Trades en DB | Rôle dans les tests |
|---|---|---|---|---|---|
| **4** | 18:49:53 | 18:50:04 | `panic_close` | 1 (achat initial) | Setup B1 / A1 |
| **5** | 18:50:09 | 18:50:14 | `user_stop` | 1 (achat initial) | — |
| **6** | 18:52:20 | 18:52:25 | `user_stop` | 0 (achat sauté) | Test C1 (`prior_bot_state_entry_avg`) |
| **7** | 18:52:30 | 18:52:40 | `panic_close` | 0 | Nettoyage test A2 |

**Impact journal de trades :** les 2 trades des cycles 4–5 s'ajoutent aux 27 du cycle 3 (+ 4 du cycle 2) pour le total de **33** lignes visibles dans `GET /api/trades/journal`.

**État après tests :** `running=false`, 0 cycle `open`, compte liquide (~5000 USDT, 0 BTC). Compatible avec le gel « pas de run longue durée sans feu vert explicite » — ces cycles étaient des tests courts documentés, pas une reprise du run organique.

## Prochain paramétrage cible (préparé, pas lancé)

| Paramètre | Actuel (annulé) | Cible |
|---|---|---|
| Pas grille | 0,25 % | **0,40 %** |
| Frais BNB | off | **on (obligatoire au démarrage)** |
| Seuil cycle | +15 USD | +15 USD (inchangé) |

Simulation viabilité : `docs/proofs/m7bis_target_config_simulation.json`  
**Bloquant** : `bnb_free = 0` sur compte Demo — approvisionnement requis avant tout nouveau run.

**Gel** : pas de nouveau run longue durée sans feu vert explicite utilisateur.

## Run organique v2 — **EN COURS** (feu vert 2026-07-05)

| Élément | Détail |
|---|---|
| Config | 0,40 % + BNB obligatoire, 5000 USDT, 20 paliers, seuil +15 |
| Durée cible | **24h** (première validation) |
| Protocole | `docs/m3_organic_long_run_v2_protocol.md` |
| Preuves | `docs/proofs/m3_organic_long_run_v2/` |
| Simulation justificative | `m7bis_target_config_simulation.json` |
| Run v1 annulé | `docs/proofs/m3_organic_long_run/` — **ne pas mélanger** |

| Docker (5 services) | **terminée** | `docker_cold_start.json` |

## Lancer

```bash
cp .env.example .env
docker compose up --build -d
```

UI : http://127.0.0.1:18080/ — API : http://127.0.0.1:18000/
