# Protocole — Instance XRP/USDT (remplace HYPER sur la 3e stack)

> **Transition 2026-07-06** : l'expérience **HYPERUSDT** est terminée (Panic Close + liquidation complète). L'instance 3 réutilise la **même infrastructure Docker** (projet `ultiumgrid_hyper`, ports `18200`/`18280`, DB `ultiumgrid_hyper`, `.env.hyper`) avec le symbole **XRPUSDT**. Historique HYPER : `docs/m3_hyper_instance_protocol.md`.

## Décision

| Avant | Après |
|---|---|
| HYPERUSDT | **XRPUSDT** |
| Instance HYPER | **Instance XRP** |
| 3 instances actives | **Toujours 3** (BTC / SOL / XRP) — pas de 4e instance |

## Isolation (inchangée)

| Élément | Instance BTC | Instance SOL | Instance XRP (ex-HYPER) |
|---|---|---|---|
| Compose | `docker-compose.yml` | `docker-compose.sol.yml` | `docker-compose.hyper.yml` |
| Projet Docker | `ultiumgrid_07_2026` | `ultiumgrid_sol` | `ultiumgrid_hyper` |
| Env | `.env` | `.env.sol` | `.env.hyper` |
| DB | `ultiumgrid` | `ultiumgrid_sol` | `ultiumgrid_hyper` |
| API / UI | 18000 / 18080 | 18100 / 18180 | **18200 / 18280** |
| UI label | UltiumGrid — Instance BTC | UltiumGrid — Instance SOL | **UltiumGrid — Instance XRP** |
| Badge symbole | — | — | **XRPUSDT** |

## Clôture HYPER — PnL final instance

Preuves : `docs/proofs/m3_hyper_instance_v1/hyper_panic_close_final.json`, `hyper_liquidation_complete.json`.

| Étape | Détail |
|---|---|
| Position avant fermeture | ~36 363 HYPER (cycle 59), mark ~0,0755 |
| Panic Close API | Liquide le solde **libre** (~6 602 HYPER) ; ordres grille annulés en DB |
| Complément manuel | Market sell **29 761,2 HYPER** @ **0,0754** (frais **2,24 USDT**) |
| HYPER résiduel | **0,0068** (dust) |
| USDT compte après liquidation | **7 787,71** |
| Référence départ compte Demo | **8 266,32 USDT** |
| **Perte économique nette compte** | **≈ −478,6 USDT** (incident WS + churn cycles 1–58 + frais) |
| PnL cycles DB (corrigé post-bug) | sum_net ≈ **+0,06 USD** — non représentatif du churn réel |

> Le Panic Close seul ne suffit pas si une grande partie de la position est **locked** dans des ordres ouverts ; la liquidation complète a nécessité un market sell explicite après annulation effective des ordres.

## Vérification candidat XRPUSDT

Script : `python3 scripts/m_xrp_instance_precheck.py`  
Preuve : `docs/proofs/m_xrp_candidate_check.json` (tableau comparatif **BTC / SOL / HYPER / XRP**).

| Critère | XRPUSDT |
|---|---|
| status | TRADING |
| tickSize | 0,0001 |
| stepSize | 0,1 |
| minNotional | 5 USDT |
| ratio notional/palier (5000/20) | **50×** (250 USDT / 5) — OK |
| net_at_gross_threshold (+15) | **13,125 USD** (identique BTC/HYPER) |
| Vol 1h/24 bars | ~2,7 % range |
| Depth top-20 | ~440k bid / ~484k ask — très liquide |

## Configuration XRP

| Paramètre | Valeur |
|---|---|
| Symbole | **XRPUSDT** |
| Capital | 5000 USDT |
| Paliers | 20 |
| Pas grille | 0,40 % |
| Seuil cycle | **+15 USD** (aligné BTC) |
| BNB fee discount | oui |

## Fix WebSocket — scénario critique

Le changement de symbole **HYPER → XRP** est exactement le scénario qui avait causé le bug WS (flux resté sur `btcusdt@bookTicker`).

**Preuve post-migration** (`docs/proofs/m3_xrp_instance_v1/launch_proof.json`) :

| Test | Résultat |
|---|---|
| `symbol` post-start | **XRPUSDT** |
| Mark API vs REST XRP | **1,1593** vs **1,1593** (ratio 0 %) |
| `mark_source` | **ws** |
| Logs bot | **`xrpusdt@bookTicker`** présent |
| Pas de résidu HYPER | `base_asset=XRP`, cycle **60** neuf |
| `restart_price_stream()` | ✅ reconnecté sur XRP |

Script non-régression : `python3 scripts/m_xrp_ws_price_regression.py`

## Lancement

```bash
cp .env.hyper.example .env.hyper   # si absent — clés Demo inchangées
python3 scripts/m_xrp_instance_precheck.py
docker compose -p ultiumgrid_hyper -f docker-compose.hyper.yml --env-file .env.hyper up -d --build
python3 scripts/m3_xrp_instance_launch.py
python3 scripts/m_xrp_ws_price_regression.py
```

UI : http://127.0.0.1:18280 — en-tête **UltiumGrid — Instance XRP**, badge **XRPUSDT**.

## Comparaison en cours (3 instances)

| Instance | Symbole | Seuil | Capital | Début run actuel |
|---|---|---|---|---|
| BTC v2 | BTCUSDT | +15 | 5000 | 2026-07-05 19:23 UTC |
| SOL v1 | SOLUSDT | +5 | 4000 | 2026-07-05 ~22:02 UTC |
| **XRP v1** | **XRPUSDT** | **+15** | **5000** | **2026-07-06 ~01:19 UTC** |

Rapport détaillé : section « Suivi run comparatif BTC / SOL / XRP » dans `docs/audit_modules.md`.

## Fichiers clés

| Fichier | Rôle |
|---|---|
| `docker-compose.hyper.yml` | Stack instance 3 (nom historique, contenu XRP) |
| `.env.hyper` | Clés API Demo (même compte qu'HYPER) |
| `scripts/m_xrp_instance_precheck.py` | Vérif exchangeInfo + comparatif 4 colonnes |
| `scripts/m3_xrp_instance_launch.py` | Config + start + preuve mark/WS |
| `docs/proofs/m3_xrp_instance_v1/` | Preuves lancement et WS |
