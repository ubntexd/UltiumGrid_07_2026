# Audit UI — Spot Demo

Date : 2026-07-04  
UI : `http://localhost:8080/` (HTTP 200)  
Preuve JSON : `docs/proofs/m8_audit_ui.json`

## Comparaisons valeur affichée (proxy UI) vs source

| Champ | UI | Source | Match |
|---|---|---|---|
| mark_price | 62556.44 | Binance ticker 62556.45 | oui (<0.5%) |
| capital.quote_free | 4999.35190534 | balances USDT | oui exact |
| capital.base_total | 9.64e-06 | balances BTC | oui exact |
| config.step_pct | 0.3 | config active DB/API | oui |
| config.num_levels | 16 | config active | oui |
| viability panel | présent | DOM `viability-panel` | oui |
| badge non placé | présent | `app.js` `badge-missing` | oui |

## Notes

- Pas de funding / levier / marge dans l’UI Spot.
- Bandeau = capital quote libre + base total.
- Indicateur viabilité : ratio brut/frais avec frais issus de `account.commissionRates.taker` (prouvé 0.001).
- Rafraîchissement : WebSocket `/ws` + poll 5 s.
