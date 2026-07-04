# Progression — UltiumGrid_07_2026

Légende : `terminé` uniquement si développement + audit conformité + tests réels + vérification croisée sont prouvés.

| Étape | Statut | Preuve / notes |
|---|---|---|
| Audit initial | **terminé** | `docs/00_audit_initial.md`, `docs/proofs/00_binance_audit_raw.json` |
| Auth clés renouvelées | **terminé** | `docs/proofs/01_binance_auth_new_keys.json` — `canTrade=true`, wallet 5000 USDT |
| Module 1 — Connecteur | **partiel** | Lecture compte/funding/WS **OK** ; placement d’ordres **bloqué** par Binance `-1007`/`502` |
| Module 2 — Base de données | **terminé** | `docs/proofs/m2_database_sql.json` + test SQL direct |
| Module 3 — Moteur de grille | **partiel** | Calcul 20 niveaux **OK** (unit) ; cycle live **non vérifié** (ordres Binance en timeout) |
| Module 4 — Coupe progressive | **partiel** | Logique unit **OK** ; scénario live **non vérifié** |
| Module 5 — Sacs | **partiel** | Code + schéma DB prêts ; vente live **non vérifiée** |
| Module 6 — Garde-fous | **partiel** | Logique unit **OK** ; déclenchement live **non vérifié** |
| Module 7 — Backend API | **terminé** (lecture) | Endpoints prouvés dans `docs/proofs/docker_api_stack.json` |
| Module 7bis — Config UI | **terminé** (hors cycle live) | 3 params appliqués en DB ; rejet levier 99 prouvé |
| Module 7ter — Marché | **terminé** (prix) | BTC/ETH prix API = Binance direct (même instant, session précédente) |
| Module 8 — UI | **partiel** | UI accessible `:8080` ; proxy API OK ; audit valeurs live limité sans grille active |
| Module 9 — Reprise crash | **non vérifié** | Code `restore_state` présent ; pas de preuve kill/restart avec ordres ouverts |
| Module 10 — Audit final | **terminé** (état réel) | `docs/audit_final.md` |
| Infrastructure Docker | **terminé** | `docker compose down -v && up --build` — 4 services Up |

---

## Blocage actif

### B3 — API d’écriture ordres Binance Futures Testnet instable

Réponses brutes observées à répétition sur `POST /fapi/v1/order` et `POST /fapi/v1/leverage` :

```
HTTP 408 {"code":-2015?} non — code -1007
{"code":-1007,"msg":"Timeout waiting for response from backend server. Send status unknown; execution status unknown."}
HTTP 502 Bad Gateway (nginx)
HTTP 503 {"code":-1008,"msg":"Request throttled by system-level protection..."}
```

Endpoints **lecture** authentifiés OK (`/fapi/v2/account`, `/fapi/v1/openOrders`, `/fapi/v2/positionRisk`, `listenKey`).

**Conséquence :** impossible de prouver honnêtement place/cancel, cycle grille, coupe, sacs, panic close, reprise avec ordres réels tant que Binance Testnet n’accepte pas les écritures.

**Action :** réessayer `POST /fapi/v1/order` ; dès HTTP 200, relancer `bot/tests/test_m1_connector_integration.py::test_place_verify_cancel_limit_order` puis start bot.

---

## Preuves clés

| Fichier | Contenu |
|---|---|
| `docs/proofs/01_binance_auth_new_keys.json` | Auth OK |
| `docs/proofs/m1_account_funding.json` | Compte + funding + filters |
| `docs/proofs/m1_websocket_reconnect.json` | WS + kill + reprise |
| `docs/proofs/m2_database_sql.json` | SQL direct |
| `docs/proofs/docker_api_stack.json` | Stack Docker + endpoints |
