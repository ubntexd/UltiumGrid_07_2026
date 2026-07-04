# Progression — UltiumGrid_07_2026

Légende : `terminé` uniquement si développement + audit conformité + tests réels + vérification croisée sont prouvés.

| Étape | Statut | Preuve / notes |
|---|---|---|
| Audit initial | **terminé** | `docs/00_audit_initial.md`, `docs/proofs/00_binance_audit_raw.json` |
| Auth clés renouvelées | **terminé** | `docs/proofs/01_binance_auth_new_keys.json` — `canTrade=true`, wallet 5000 USDT |
| Module 1 — Connecteur | **partiel** | Lecture/WS OK ; anti-doublon **unit** + vérif **integration** (`m1_antiduze_post_1007.json`) ; `retry_exhausted` forcé (`m1_retry_exhausted.json`) ; placement live toujours timeout Binance |
| Module 2 — Base de données | **terminé** | `docs/proofs/m2_database_sql.json` + test SQL direct |
| Module 3 — Moteur de grille | **partiel** | Calcul 20 niveaux **OK** (unit) ; cycle live **non vérifié** (ordres Binance en timeout) |
| Module 4 — Coupe progressive | **partiel** | Unit OK (qty réelle + incomplets) ; live **non vérifié** |
| Module 5 — Sacs | **partiel** | Qty réelle / `reconciliation_unavailable` codés ; live **non vérifié** |
| Module 6 — Garde-fous | **partiel** | Panic/stop sur `positionRisk` frais ; live **non vérifié** |
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

**URL REST réelle (avant correction) :** `https://testnet.binancefuture.com` (hardcodée dans le connecteur).  
**URL officielle docs Binance :** `https://demo-fapi.binance.com` (WS `wss://demo-fstream.binance.com`).

Correction appliquée (2026-07-04) : connecteur + `.env` pointent vers `demo-fapi`.  
Les clés actuelles authentifient `GET /fapi/v2/account` sur **les deux** bases (HTTP 200, `canTrade=true`).

`POST /fapi/v1/order` reste en échec sur **demo-fapi** aussi :

```
HTTP 408 {"code":-1007,"msg":"Timeout waiting for response from backend server. Send status unknown; execution status unknown."}
```

Preuve : `test_place_verify_cancel_limit_order` → `RetryExhaustedError` après 5 tentatives (réponses `Order does not exist` en vérif post-timeout).

**Hypothèse restante :** clés créées hors du portail demo trading actuel (`demo.binance.com`) — à régénérer côté utilisateur si le matching engine continue de timeout.

**Action :** régénérer des clés API sur l’infra demo, mettre à jour `.env`, relancer `test_place_verify_cancel_limit_order`.

---

## Preuves clés

| Fichier | Contenu |
|---|---|
| `docs/proofs/01_binance_auth_new_keys.json` | Auth OK |
| `docs/proofs/m1_account_funding.json` | Compte + funding + filters |
| `docs/proofs/m1_websocket_reconnect.json` | WS + kill + reprise |
| `docs/proofs/m2_database_sql.json` | SQL direct |
| `docs/proofs/docker_api_stack.json` | Stack Docker + endpoints |
