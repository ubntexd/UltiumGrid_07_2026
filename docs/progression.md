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

**Diagnostic prouvé (2026-07-04, `docs/proofs/m1_order_diagnosis.json`) :**

| Appel | Résultat |
|---|---|
| `GET /fapi/v1/ping` | 200 |
| `GET /fapi/v2/account` | 200 `canTrade=true` |
| `POST /fapi/v1/order/test` | **200** (signature + format OK, pas de matching engine) |
| `POST /fapi/v1/order` | **-1007** (matching engine timeout) |

Sources web convergentes (CCXT #26487, go-binance #765, bots demo 2025–2026) :
- L’ancien Futures Sandbox est déprécié.
- Les clés **ne sont pas interchangeables** entre ancien testnet et Demo Trading.
- Un compte peut répondre en lecture sur `demo-fapi` avec d’anciennes clés, mais le **matching engine** reste en `-1007`.

**Solution non négociable (action humaine) :**

1. Aller sur https://demo.binance.com (login GitHub recommandé, pas l’ancien `testnet.binancefuture.com`).
2. API Management → Create API → copier Key + Secret.
3. Mettre à jour `.env` (`BINANCE_FUTURES_TESTNET_API_KEY` / `_SECRET`).
4. Relancer : `python scripts/diagnose_binance_orders.py` puis `pytest ...::test_place_verify_cancel_limit_order`.

Le code utilise déjà `https://demo-fapi.binance.com`, POST en body form-urlencoded, anti-doublon post-`-1007`.

---

## Preuves clés

| Fichier | Contenu |
|---|---|
| `docs/proofs/01_binance_auth_new_keys.json` | Auth OK |
| `docs/proofs/m1_account_funding.json` | Compte + funding + filters |
| `docs/proofs/m1_websocket_reconnect.json` | WS + kill + reprise |
| `docs/proofs/m2_database_sql.json` | SQL direct |
| `docs/proofs/docker_api_stack.json` | Stack Docker + endpoints |
