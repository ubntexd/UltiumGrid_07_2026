# Audit final de conformité globale

Date : 2026-07-04  
Projet **non déclaré prêt** pour trading live testnet tant que B3 (écriture ordres Binance) n’est pas levé.

## Conforme avec preuve

| Exigence | Preuve |
|---|---|
| Connectivité publique Binance Testnet | `docs/proofs/00_binance_audit_raw.json` |
| Auth compte Futures (`canTrade=true`, soldes) | `docs/proofs/01_binance_auth_new_keys.json` |
| Funding + exchangeInfo filters BTC | `docs/proofs/m1_account_funding.json` |
| WebSocket mark price + reprise après kill | `docs/proofs/m1_websocket_reconnect.json` |
| Schéma DB + SQL direct | `docs/proofs/m2_database_sql.json` |
| 20 niveaux grille (unit) | pytest `test_compute_20_levels_arithmetic` |
| Coupe 10/14 + réarmement (unit) | pytest `test_cut_at_level_10_and_14` |
| Stop dur / circuit breaker (unit) | pytest `test_hard_stop_and_circuit_breaker` |
| Docker compose 4 services Up | `docs/proofs/docker_api_stack.json`, `docker compose ps` |
| API Running/History/PnL/Bags/Config/Market/Margin | `docker_api_stack.json` |
| Rejet config hors bornes | `leverage=99` → erreur |
| Application config (levier 3, step 0.3, trigger 12) | SQL `configurations` id=3 active |
| UI HTTP 200 + proxy | `frontend_http: 200` |
| Prix BTC/ETH alignés Binance (session test) | comparaisons documentées audit_ui / progression |
| Volume Docker `pgdata` | `docker-compose.yml` volumes |

## Écarts assumés

| Écart | Justification |
|---|---|
| `docs/spec.md` dérivé du prompt (cahier des charges fichier absent) | Documenté dans `questions_ouvertes.md` Q1 |
| Circuit breaker défaut −40 USD | Q3 — choix prudent |
| Alertes = DB + logs, pas Telegram | Q4 — pas de credentials canal |
| Simulation config = replay cycles, pas tick-à-tick | Documenté dans réponse API `method` |
| Bot et backend découplés via `bot_state.commands` | Architecture nécessaire multi-conteneurs |

## Non vérifié (blocage Binance écriture)

| Exigence | Détail |
|---|---|
| Place / cancel ordre limite | `-1007` / `502` / `-1008` répétés |
| Cycle grille complet +15 | dépend ordres |
| Coupe live paliers 10/14 | dépend position réelle |
| Vente sac live | dépend ordres |
| Panic close live | dépend ordres |
| Reprise crash avec ordres ouverts | dépend ordres |
| set_leverage | même timeout `-1007` |

Réponses brutes typiques :

```
{"code":-1007,"msg":"Timeout waiting for response from backend server. Send status unknown; execution status unknown."}
```

## Non vérifiable spécifiquement en testnet

- Comportement sous forte latence réseau production
- Liquidation réelle / ADL
- Funding payment exact sur longue durée (funding lu OK, cumul cycle non observé)

## Verdict

**Infrastructure + lecture marché/compte + UI + config + logique unitaire : opérationnels avec preuves.**

**Trading automatisé end-to-end : non prêt** — en attente du rétablissement de l’API d’écriture Binance Futures Testnet.
