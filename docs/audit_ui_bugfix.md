# Audit UI — bugs Mark / Capital / Niveaux

Date : 2026-07-04  
Preuves brutes : `docs/proofs/ui_bugfix_raw.json`, `docs/proofs/ui_bugfix_analysis.json`

## Méthode

Au même instant (après levée du ban IP) :

1. `GET /api/v3/account` (Binance, brut)
2. `GET /api/v3/ticker/price?symbol=BTCUSDT`
3. `GET /api/v3/openOrders?symbol=BTCUSDT`
4. `GET http://127.0.0.1:8000/api/running`
5. `GET http://127.0.0.1:8000/api/capital`

Pendant le ban (avant correctifs), capture live de `/api/running` :

- `mark_price: null`
- `capital: { "error": "418 {\"code\":-1003,\"msg\":\"Way too much request weight used; IP banned until 1783173740800...\"}" }`
- `/api/capital` → HTTP 500 (exception non catchée)

---

## 1. Mark vide dans État alors que l’en-tête montrait un prix

### Sources (avant correctif)

| Champ UI | Code | Source |
|---|---|---|
| En-tête `#live-price` | `renderRunning` : n’écrit **que si** `mark != null` | `s.mark_price` **ou ancienne valeur laissée telle quelle** |
| Panneau État « Mark » | `fmt(s.mark_price)` à chaque refresh | `s.mark_price` uniquement |

Les deux lisent le même champ API `mark_price` (ticker Binance via `build_status` → `ticker_price`).  
Mais si le ticker échoue (`mark_price: null`), l’en-tête **conservait** le dernier texte affiché, alors que l’État était **réécrit** avec `fmt(null) = "—"`.

Preuve pendant ban :

```json
"mark_price": null
```

alors que l’en-tête pouvait encore afficher `BTCUSDT 62555.97` (valeur résiduelle DOM).

### Correctif

- Une seule valeur d’affichage `markText` dérivée de `s.mark_price`, appliquée **aux deux** champs à chaque refresh.
- Backend : en cas d’échec ticker, renvoie le dernier prix connu + `mark_stale` / `mark_error` (pas de divergence silencieuse).
- Cache ticker TTL 2 s côté connecteur.

### Preuve après correctif (2026-07-04T14:02:53Z)

| Source | Valeur |
|---|---|
| Binance `GET /api/v3/ticker/price` | `"price": "62636.00000000"` (HTTP 200) |
| `/api/running.mark_price` | `62636.0` |
| `mark_stale` / `mark_error` | `false` / `null` |
| Match | **oui** |

---

## 2. Capital / BASE / canTrade à "—"

### Cause prouvée (pas une supposition)

1. **Binance IP ban `-1003` / HTTP 418** pendant les polls agressifs.
2. **`build_status` appelait `account()` jusqu’à 4 fois** par refresh (`balance_free` ×2 + `balance_total` + `canTrade`), toutes les **2 s** (broadcast WS) + toutes les **5 s** (UI) → explosion du poids API.
3. Exception avalée → `capital: { "error": "418 ..." }`.
4. UI lisait `m.quote_free`, `m.base_total`, `m.canTrade` **sans lire `m.error`** → `fmt(undefined) = "—"` et `canTrade ?? "—"` (**erreur silencieuse**).
5. `/api/capital` **plantait en 500** (pas de try/except) au lieu de renvoyer l’erreur.

### Appel réel `GET /api/v3/account` (après levée du ban)

HTTP **200**, extrait :

```json
{
  "canTrade": true,
  "balances_nonzero": {
    "BTC": { "free": "0.00000964", "locked": "0.00000000" },
    "USDT": { "free": "4999.35190534", "locked": "0.00000000" },
    "USDC": { "free": "5000.00000000", "locked": "0.00000000" }
  }
}
```

Réponse brute complète : `docs/proofs/ui_bugfix_raw.json` → `account_full`.

### Chemin jusqu’à l’UI

`GET /api/v3/account` → `capital_snapshot()` (1 seul account, cache TTL 8 s) → `running.capital` / `GET /api/capital` → `renderRunning` (`margin-banner`).

| Champ | Binance | `/api/running.capital` | Match |
|---|---|---|---|
| USDT libre | `4999.35190534` | `4999.35190534` | oui |
| BTC total | `0.00000964` | `9.64e-06` | oui |
| canTrade | `true` | `true` | oui |

### Correctifs

- `capital_snapshot()` : un seul `GET /api/v3/account`, cache, dernier snapshot en `stale` si erreur.
- UI : si `capital.error` et pas de soldes → affiche l’erreur (plus de "—" silencieux).
- `/api/capital` utilise `capital_snapshot` (plus de 500).
- Backoff local sur ban 418 (ne pas marteler Binance jusqu’à `banned until`).
- Broadcast WS : 5 s au lieu de 2 s.

---

## 3. Table Niveaux vs `GET /api/v3/openOrders`

### Instant T = 2026-07-04T14:02:53Z

| Source | Résultat |
|---|---|
| Binance `openOrders` | HTTP 200, **`[]`** (0 ordres) |
| UI / DB levels | 10× `canceled` (BUY, avec `order_id`), 10× `pending` (SELL, sans `order_id`) |

Comparaison niveau par niveau (`exchange_orders.levels_vs_openOrders`) :

- Aucun niveau `status=open` absent de `openOrders`
- Aucun niveau `canceled`/`pending` encore présent dans `openOrders`
- Aucun ordre orphelin dans `openOrders`

**`mismatches: []` — pas de désync DB↔exchange à cet instant.**

La table n’est **pas** un miroir live de `openOrders` : elle montre l’état grille en DB (BUY annulés + SELL en attente de fill, design Spot).  
Elle n’était **pas** « en cache obsolète » par rapport à l’exchange : les BUY `canceled` sont bien absents de `openOrders`, les SELL `pending` n’ont jamais d’`order_id` tant que non placés.

### Correctif de transparence

- `/api/running` expose `exchange_orders` (openOrders + `levels_vs_openOrders` + `mismatches`).
- UI : ligne de sync sous Niveaux (`openOrders=N | écarts=…`) et badge si un niveau `open` n’est plus sur l’exchange.

---

## 4. Fichiers modifiés

| Fichier | Changement |
|---|---|
| `bot/ultiumgrid/connector/binance_spot.py` | cache account/ticker/openOrders, backoff 418, `capital_snapshot` |
| `backend/app/main.py` | mark unifié + stale/error, capital_snapshot, exchange_orders, broadcast 5s |
| `bot/ultiumgrid/bot_runner.py` | même logique mark/capital |
| `frontend/app.js` | Mark identique header/État, erreur capital visible, sync niveaux |

---

## Verdict

| Bug | Cause prouvée | Correctif |
|---|---|---|
| Mark État "—" / header prix | DOM header non mis à jour si `mark_price=null` (ban ticker) | Même `markText` pour les deux + cache/stale backend |
| Capital "—" | `capital.error` (418) ignoré par l’UI ; multi-`account()` → ban | Snapshot unique + affichage erreur + caches |
| Niveaux « faux » | Non prouvé : alignés avec `openOrders=[]` | Sync explicite UI/API pour détecter de vrais écarts |


## 5. Validation visuelle post-correctif

Capture : `docs/proofs/ui_bugfix_after.png` (navigateur MCP + chromium).

Observé dans le DOM / capture :

- En-tête : `BTCUSDT 62626.01`
- État Mark : `62626.01` (**identique**)
- Bandeau : `Capital USDT libre: 4999.35 | BTC total: 0.000010 | canTrade: true`
- Aligné avec `GET /api/v3/account` et ticker au même instant (voir §1–2)

---

## 6. Cycles dupliqués (deux `status=open` simultanés)

### Constat

History affichait cycles **1** et **2** tous deux `open` ; État montrait `Cycle: 2`.

### 6.1 Timeline SQL (preuve directe)

```sql
SELECT id, symbol, status, center_price, opened_at, closed_at, close_reason FROM cycles ORDER BY id;
```

| id | status (avant fix) | center | opened_at (UTC) | levels |
|---|---|---|---|---|
| 1 | **open** | 62533 | 2026-07-04 08:52:47 | 20 niveaux, **tous `status=error`**, **aucun `order_id`** |
| 2 | **open** | 62514 | 2026-07-04 12:00:53 | 10 BUY avec `order_id` 45925072189–198, 10 SELL `pending` |

`bot_state.main` / `heartbeat` : `cycle_id=2`, `running=true`.

`order_attempts` : **uniquement** à 12:00:53 (10× BUY `success` pour le cycle 2). **Aucune** ligne entre 08:52 et 12:00.

`trades` : 0 lignes.

**Conclusion timeline :** le cycle 1 a été **abandonné sans fermeture** (niveaux en `error`, aucun ordre placé — époque Futures / échecs placement). À 12:00, un **nouveau** cycle 2 a été créé (`_open_new_cycle`) **sans clôturer** le cycle 1 en DB.

### 6.2 Redémarrage / Module 9

Logs bot actuels (container recréé depuis) ne conservent pas la fenêtre 08:52–12:00. Comportement code prouvé :

```python
# start() — avant correctif
if not self.engine.state.active:
    self._open_new_cycle()  # INSERT cycles status=open, sans fermer les open existants
```

`restore_state()` rechargeait `cycle_id` depuis `bot_state` et **ne fermait pas** les autres lignes `cycles.status='open'`.

Scénario confirmé par les données (pas seulement hypothèse) :

1. Cycle 1 ouvert à 08:52, placement en échec (`error`, pas d’`order_id`).
2. Plus tard (migration Spot / nouveau Start), `engine.state.active` était faux → `_open_new_cycle()` → cycle 2.
3. Cycle 1 restait `open` en DB.

Ce n’est **pas** un bug d’affichage : deux lignes SQL `status='open'`.

### 6.3 Verrou anti-doublon (avant correctif)

| Niveau | Présent ? |
|---|---|
| Contrainte DB `UNIQUE (symbol) WHERE status='open'` | **Non** (seulement `cycles_pkey`, `ix_cycles_symbol`) |
| Garde applicative dans `_open_new_cycle` / `restore_state` | **Non** |

Lacune de conception, pas un incident isolé.

### 6.4 Impact Binance (preuve brute)

Preuve : `docs/proofs/m_cycles_duplicate_binance.json`

| Check | Résultat |
|---|---|
| `GET /api/v3/openOrders` | `[]` |
| Cycle 1 `order_id` en DB | **tous null** → aucun ordre cycle 1 sur l’exchange |
| Cycle 2 orderIds 45925072189–198 | présents dans `allOrders`, status `CANCELED` |
| Autres ordres fenêtre | tests manuels / diagnostics (prix 40000, etc.), **pas** une 2ᵉ grille cycle 1 |

**Impact trading :** doublon **cantonné à la DB**. Pas deux grilles live en parallèle sur le compte. Seul le cycle 2 a placé des ordres.

### 6.5 Correctifs appliqués

1. **Applicatif** (`bot_runner.py`) :
   - `_ensure_single_open_cycle(reason=...)` : ferme les open orphelins, garde `cycle_id` courant ou le plus récent.
   - Appelé dans `restore_state` (`orphan_on_restore`) et `start` (`orphan_on_start`).
   - `_open_new_cycle` ferme d’abord tout open du symbole (`superseded_by_new_cycle`).
2. **DB** : index partiel unique
   ```sql
   CREATE UNIQUE INDEX uq_cycles_one_open_per_symbol ON cycles (symbol) WHERE status = 'open';
   ```
   (aussi déclaré dans `models.py` pour `create_all` sur nouvelles bases).
3. **Données** : cycle 1 fermé `close_reason=orphan_superseded`.
4. **UI** : PnL Analysis affiche `Cycles clôturés : N (W…/L…)` (ne compte que les `closed`, libellé explicite).

### 6.6 Preuves de non-régression

**A. Fermeture orphelin au restore** (index temporairement retiré, insert cycle 3 `open`, `docker compose restart bot`) :

```
WARNING Closed 1 orphan open cycle(s) for BTCUSDT keep_id=2 reason=orphan_on_restore
```

SQL après : cycle 3 `closed` / `orphan_on_restore` ; **un seul** open (id=2).

**B. Contrainte DB** : second `INSERT ... status='open'` →

```
ERROR: duplicate key value violates unique constraint "uq_cycles_one_open_per_symbol"
```

**C. Restart volontaire avec cycle actif** :

```
State restored cycle_id=2 running=True
```

```sql
SELECT count(*) FROM cycles WHERE symbol='BTCUSDT' AND status='open';  -- → 1
```

### 6.7 Module 9

La reprise recharge bien `bot_state` (`cycle_id=2`), mais **ne garantissait pas** l’invariant « un seul open en table `cycles` ». Validation Module 9 **révoquée puis re-prouvée** avec les tests A–C ci-dessus (voir `progression.md`).

---

## 7. Saut de prix pendant ban IP (graphique Prix)

### Constat

Courbe « Prix + fourchette de grille » : plateau ~62 550 puis marche verticale vers ~62 650, calée sur la fenêtre du ban `-1003` (13:56 → 14:02 UTC).

### Hypothèses

| | Description |
|---|---|
| **A (bénigne)** | Prix réel a bougé ; aucun tick observé pendant le ban ; premier point post-ban = vrai marché. Marche = artefact d’échantillonnage / axe catégorie Chart.js. |
| **B (problématique)** | Fallback `mark_stale` a **inséré** des points plats répétés en DB pendant le ban. |

### 7.1 SQL `price_ticks` (13:55 → 14:03 UTC)

Schéma : **pas de colonne** `mark_stale` / `mark_error` (jamais stocké à l’insertion).

| id | ts (UTC) | price |
|---|---|---|
| 865 | 13:56:00.294 | 62556.34 |
| 866 | 13:56:05.339 | 62556.35 |
| 867 | 13:56:10.388 | 62556.34 |
| — | **trou 13:56:10 → 14:02:21 (~371 s)** | **0 ligne** |
| 868 | 14:02:21.517 | 62636.00 |
| 869–872 | 14:02:26–41 | 62636.00 (×5) |
| 873–875 | 14:02:46–56 | 62636.01 (×3) |

Points dans le trou du ban : **0**.  
Valeurs identiques **pendant** le ban : **N/A** (aucune ligne).  
Les répétitions `62636` sont **après** la levée du ban (marché réellement plat à ce niveau), pas un fallback stale.

Preuve JSON : `docs/proofs/m_price_gap_ban_analysis.json`.

### 7.2 Klines Binance indépendantes (`GET /api/v3/klines` interval=1m)

Mouvement **progressif** pendant la fenêtre sans ticks bot :

| minute UTC | open | high | low | close |
|---|---|---|---|---|
| 13:56 | 62556.34 | 62570.33 | 62556.34 | 62570.32 |
| 13:57 | 62570.32 | 62585.53 | 62566.74 | 62585.53 |
| 13:58 | 62585.53 | 62588.85 | 62585.53 | 62588.85 |
| 13:59 | 62588.85 | 62588.85 | 62588.84 | 62588.84 |
| 14:00 | 62588.84 | 62604.00 | 62585.53 | 62604.00 |
| 14:01 | 62604.00 | 62653.68 | 62603.99 | 62638.01 |
| 14:02 | 62638.00 | 62638.25 | 62636.00 | **62636.00** |

- Dernier tick bot avant ban : **62556.34** (= low/open kline 13:56).
- Premier tick bot après ban : **62636.00** (= **close exact** kline 14:02).
- Marché a monté ~**+80 USDT** de façon continue (pas un gap inventé).

### 7.3 Code d’insertion

`price_ticks` écrit uniquement depuis :

1. `BotRunner._snapshot_pnl(mark)` appelé en fin de `tick()`, avec `mark = float(ticker_price(...))` **en tête** de `tick()`.
2. Branche `not running` dans `main_loop` : `ticker_price` puis `PriceTick(...)` ; `except: pass`.

En cas d’échec ticker (418 ban) :

- `tick()` lève → `main_loop` logue `tick failed` → **aucune insertion**.
- Branche idle : `except: pass` → **aucune insertion**.

Comportement = **(a) n’insère rien**. Pas (b) ni (c).  
`mark_stale` n’existe que dans la réponse API `/api/running` (affichage header/État) ; **jamais** écrit dans `price_ticks`.

### 7.4 Verdict

**Hypothèse A confirmée.** Hypothèse B **réfutée** (0 point en DB pendant le ban ; klines montrent un mouvement réel cohérent avec le premier tick post-ban).

La « marche verticale » est un **artefact d’affichage** : Chart.js utilise un axe X **catégoriel** (un slot par point, espacement égal), donc le segment entre le dernier point 13:56:10 et le premier 14:02:21 apparaît comme un saut immédiat entre deux catégories adjacentes, alors qu’il y a 6+ minutes sans donnée.

### 7.5 Correctif

**Aucun** correctif de données ni de logique d’insertion requis pour cette fenêtre. Historique laissé intact (trou = absence d’observation, pas des stale inventés).

Note pour lecture future du graphique : un trou temporel long entre deux `price_ticks` se lit comme une discontinuité d’observation, pas comme un mouvement instantané du marché.

---

## 8. Boutons de contrôle (Start / Stop / Panic Close)

Preuve complète : `docs/proofs/m_control_buttons.json` (2026-07-04T14:49Z).

### Écarts initiaux (avant correctif)

| Bouton | Problème prouvé |
|---|---|
| **Stop** | Ne faisait que `running=False` — **n’annulait pas** les ordres, **ne fermait pas** le cycle en DB |
| **Panic** | Vendait le free base mais **ne fermait pas** le cycle (`close_reason` absent) ; `panic=True` bloquait tout Start ultérieur même en no-op |
| **Start ×2** | Pas de second cycle (OK grâce à `active`), mais **aucun message UI** clair |

### Correctifs

- `stop()` : `cancel_all_grid_orders` (+ fallback par ordre + résidus `openOrders`), cycle `closed` / `user_stop`, **position conservée**
- `panic()` : annule ordres, vend si `free >= min_qty`, clôture cycle `panic_close` + sacs ; **noop** si rien à vendre (`panic` flag non bloquant)
- `start()` : si déjà `running+active` → `{already_running: true, message: "..."}` sans nouveau cycle
- API : messages + `GET /api/last_command` ; UI affiche le retour

### Résultats avant/après (re-test)

#### Start depuis arrêté — **conforme**

| | Avant | Après |
|---|---|---|
| `running` | false | **true** |
| `openOrders` | 0 | **8** BUY |
| cycle DB | aucun open | **id=7 open** |
| last_command | — | `Démarré`, `cycle_id=7` |

#### Start déjà actif — **conforme**

| | Avant | Après |
|---|---|---|
| `cycle_id` | 7 | **7** (inchangé) |
| cycles open | 1 | **1** |
| `openOrders` | 8 | 8 |
| HTTP | — | `already_running: true`, message anti-doublon |

#### Stop avec ordres — **conforme**

| | Avant | Après |
|---|---|---|
| `running` | true | **false** |
| `openOrders` | 8 | **0** |
| BTC free | 0.00000964 | **0.00000964** (pas de vente) |
| cycle | 7 open | **7 closed / user_stop** |

#### Panic avec ordres ouverts — **conforme** (vente marché : dust)

| | Avant | Après |
|---|---|---|
| `running` | true | **false** |
| `openOrders` | 8 | **0** |
| cycle | 8 open | **8 closed / panic_close** |
| BTC | 0.00000964 | 0.00000964 |

Solde base **&lt; min_qty** → pas d’ordre MARKET (`sold_orders=[]`, `noop: true` pour la jambe vente). Annulation des ordres + clôture cycle prouvées. Vente 100 % d’une position **≥ min_qty** : chemin code `panic_close` (lecture `balances` juste avant) — **non rejoué live** faute de position suffisante sur le compte demo.

#### Panic no-op — **conforme**

| | Avant | Après |
|---|---|---|
| `running` | false | false |
| `openOrders` | 0 | 0 |
| HTTP | ok | ok, pas d’exception |
| last_command | — | `noop: true`, `Rien à fermer` |

### Verdict

Tous les cas listés sont **conformes** après correctif, avec preuve Binance + DB. Seule limite documentée dans ce paragraphe : jambe **vente marché** du panic non exercée dans `m_control_buttons.json` (dust &lt; `min_qty`) — **levée** par le test contrôlé §9 ci-dessous.

---

## 9. Panic close — vente réelle (test contrôlé)

> **Position créée artificiellement à des fins de test — ne reflète pas un fonctionnement organique de la grille.**  
> Ce test valide uniquement la mécanique de vente du panic close (Module 6), pas le moteur de grille (Module 3).

Preuve brute : `docs/proofs/m_panic_real_sell.json`  
Méthode : `POST /api/panic` (équivalent bouton UI).

### 9.1 Filtres réels utilisés (`GET /api/v3/exchangeInfo?symbol=BTCUSDT`)

| Filtre | Valeur |
|---|---|
| `LOT_SIZE.minQty` | `0.00001000` |
| `LOT_SIZE.stepSize` | `0.00001000` |
| `NOTIONAL.minNotional` | `5.00000000` (`applyMinToMarket: true`) |
| Prix ticker au dimensionnement | `62812.00` |
| Qty BUY artificielle | `0.00010000` (notional estimé ≈ **6.28 USDT** &gt; 5) |

### 9.2 Création de la position artificielle

| Étape | Résultat |
|---|---|
| Tag | `purpose=test_artificial_position` / `test_artificial_position=true` |
| Ordre | MARKET BUY `orderId=…`, `executedQty=0.00010000`, `cummulativeQuoteQty=6.281201` |
| Prix moyen achat | **62812.01** |
| BTC free avant | `0.00000964` |
| BTC free après buy | **`0.00010954`** (au-dessus du minNotional) |
| Frais buy | commission en BTC convertie ≈ **0.006281 USDT** |

### 9.3 Panic close — avant / après

| Champ | Avant panic | Après panic |
|---|---|---|
| `running` | false (bot stoppé avant le buy artificiel) | **false** |
| `openOrders` | 0 | **0** |
| BTC free | `0.00010954` | **`0.00000954`** (dust &lt; minQty, cohérent) |
| `sold_orders` | — | **1 ordre MARKET SELL FILLED** |
| Cycle DB | aucun `open` | dernier cycle `closed` / **`panic_close`** |
| `noop` | — | **`false`** |
| Message | — | `Panic close exécuté` |

**Vente marché réelle :**

| Champ | Valeur |
|---|---|
| `orderId` | `45956984843` |
| `type` / `side` | `MARKET` / `SELL` |
| `executedQty` | `0.00010000` |
| `cummulativeQuoteQty` | `6.282113` |
| Fill price | **62821.13** |
| Mark au clic | **62821.13** |
| Slippage vs mark | **0.0 %** |
| Commission sell | `0.00628211` USDT (`tradeId=254282190`) |

### 9.4 Coût réel du test (Module 7quater)

| Poste | USDT |
|---|---|
| Achat (quote) | 6.281201 |
| Vente (quote) | 6.282113 |
| Gross PnL | +0.000912 |
| Frais buy | 0.006281 |
| Frais sell | 0.006282 |
| **Net PnL (après frais)** | **≈ −0.01165** |
| **Total frais** | **≈ 0.01256** |

### 9.5 Effets de bord

- Aucun cycle `open` résiduel (`count=0`).
- Pas de second cycle créé par le panic.
- Dust résiduel `0.00000954` BTC = solde antérieur au buy artificiel (non vendable &lt; `minQty`) — **attendu**, pas une position grille.

### Verdict §9

**Conforme.** La jambe vente marché du panic close est prouvée en exécution réelle (`sold_orders` non vide, fill MARKET, solde BTC ramené au dust).

---

## 10. Floating PnL live (tick WebSocket, sans fill)

Exigence prompt Module 3 : le Floating/Gross doit bouger à chaque micro-mouvement de prix, comme sur Binance, **même sans fill**.

### Implémentation

- Thread bot `ultium-ws-price` : `bookTicker` → `update_floating(mark)` → `bot_state.live_pnl` (throttle 100 ms).
- `build_status` : `mark_source=ws` si `live_pnl` &lt; 3 s ; **recalcule toujours** `floating = (mark - entry_avg) * position_qty` (jamais le cache du dernier fill).

### Preuve `docs/proofs/m3_floating_live_tick.json`

> Position acquise par BUY MARKET artificiel (`purpose=test_floating_pnl`) pour avoir `position_qty` et `entry_avg` non nuls — **pas un fill de grille**.

| | T1 | T2 (~2 s plus tard) |
|---|---|---|
| mark (WS) | 62874.515 | **62887.995** |
| floating | 0.04466865 | **0.04614256** |
| gross | = floating | = floating |
| position_qty | 0.00010934 | 0.00010934 (stable) |
| entry_avg | 62465.985 | 62465.985 (stable) |
| openOrders | 0 | 0 |
| fills entre T1/T2 | **aucun** (`allOrders` ids identiques) |

Δmark = **+13.48** → Δfloating attendu = 13.48 × 0.00010934 = **+0.0014739** = Δfloating observé.

**Conforme.**
