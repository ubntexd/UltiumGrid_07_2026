# Audit UI

Date : 2026-07-04  
UI : `http://localhost:8080/` (HTTP 200 prouvé)  
Méthode : comparaison API proxy frontend ↔ sources Binance / DB au même instant.

## Accessibilité

| Check | Résultat |
|---|---|
| `GET http://localhost:8080/` | HTTP 200, HTML UltiumGrid |
| `GET http://localhost:8080/api/running` (proxy nginx) | JSON status |
| Onglets Running / History / PnL / Bags / Config / Market | présents dans le DOM (`frontend/index.html`) |

## Comparaisons valeur affichée vs source

Valeurs lues via le proxy UI (`:8080/api/running`) et sources :

| Champ UI (via proxy) | Valeur | Source | Valeur source | Match |
|---|---|---|---|---|
| mark_price | `62533.0` | Binance `GET /fapi/v1/ticker/price` (même session antérieure) | `62533.00` | **oui** |
| margin.totalWalletBalance | `5000.00000000` | Binance `/fapi/v2/account` via `/api/margin` | `5000.00000000` | **oui** |
| margin.availableBalance | `5000.00000000` | idem | `5000.00000000` | **oui** |
| margin.canTrade | `true` | idem | `true` | **oui** |
| config.leverage | `3` | DB `configurations` id=3 `is_active=t` | `3` | **oui** |
| config.step_pct | `0.3` | DB | `0.3` | **oui** |
| config.cycle_trigger_usd | `12` | DB | `12` | **oui** |
| running | `false` | DB `bot_state.main.running` | `false` | **oui** |
| bags | `[]` | DB `bags` status=open | vide | **oui** |
| grid.levels | `[]` | bot_state | `[]` | **oui** |

Preuve agrégée : `docs/proofs/docker_api_stack.json`.

## Placeholders / données statiques

Aucun prix ou solde codé en dur dans `frontend/app.js` : toutes les valeurs viennent de `/api/*`.

## Couleurs gain/perte

Classes CSS `.pos` (vert) / `.neg` (rouge) appliquées via `pnlClass()` — **non vérifié visuellement avec un PnL non nul** (aucun cycle clos en DB).

## Temps réel

WebSocket `/ws` + refresh 5 s sur `/api/running`.  
**non vérifié** : capture avant/après d’une valeur qui change sous nos yeux pendant un cycle actif (grille inactive faute d’ordres Binance).

## Écarts assumés

- Pas de screenshot navigateur automatisé dans cet environnement pour chaque pixel ; audit basé sur le DOM servi et les réponses JSON du proxy identiques à l’API backend.
