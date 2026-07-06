# Stratégie gains & simulation seuil +10 vs +15

*Généré le 2026-07-06T08:11:47.219888+00:00*

## Diagnostic (état au 06/07/2026)

Le grid profit est positif (~+7 USD sur les cycles ouverts) mais le **floating**
en marché baissier domine (~−66 USD). L'edge réel vient des **cycles clos au trigger**.

| Instance | Réalisé net (clos) | Total Profit ouvert | Grid | Floating |
|---|---:|---:|---:|---:|
| BTC | +21.59 | -15.52 | +3.12 | -18.64 |
| SOL | +15.38 | -24.75 | +2.49 | -27.24 |
| XRP | +0.06 | -12.05 | +3.15 | -15.20 |

## Leviers stratégiques

1. **Trigger plus bas** (+8 à +10 sur BTC/XRP, garder +5 sur SOL)
2. **Marché en range** — éviter cycles longs en tendance baissière
3. **Pas 0,40–0,50 %** avec BNB (ratio gain/frais > 2,5×)
4. **Cas A/B** — recentrage si grille inactive

## Simulation +10 vs +15 (replay `pnl_snapshots`)

> `pnl_snapshots.grid_pnl` = **gross cycle** (grid + floating), même métrique que le trigger bot.

### BTC (BTCUSDT)

- Cycles analysés (clos) : **2**
- Net réalisé actuel (seuil +15 ou autre) : **+28.29 USD**
- Net contre-factuel si clôture au **premier** gross ≥ +10 : **+0.00 USD**
- **Delta** (cycles clos, snapshots disponibles) : **-28.29 USD**
- ⚠ Cycles sans snapshots dans la fenêtre : **[8, 9]** (pas de replay possible — garder le résultat réel +15)
- **Cycle ouvert** : seuil +10 aurait déclenché à **2026-07-05 23:05:12 UTC** (gross **10.42 USD**, net estimé **~5.93 USD**)
- Gain évité vs état actuel du cycle ouvert : **~+21.45 USD** (live gross maintenant : -15.52 USD)

| Cycle | Raison réelle | Net réel | 1er ≥+10 | Net CF@+10 | Δ |
|---:|---|---:|---|---:|---:|
| 8 | trigger_15 | +12.89 | — | — | — |
| 9 | trigger_15 | +15.39 | — | — | — |
| 10 | open | -1.12 | 10.42 | +5.93 | — |

### XRP (XRPUSDT)

- Cycles analysés (clos) : **0**
- Net réalisé actuel (seuil +15 ou autre) : **+0.00 USD**
- Net contre-factuel si clôture au **premier** gross ≥ +10 : **+0.00 USD**
- **Delta** (cycles clos, snapshots disponibles) : **+0.00 USD**
- Cycle ouvert : max gross observé **5.11 USD** — pas encore +10

| Cycle | Raison réelle | Net réel | 1er ≥+10 | Net CF@+10 | Δ |
|---:|---|---:|---|---:|---:|
| 60 | open | +0.00 | — | — | — |


## Conclusion

| Instance | Recommandation | Justification (données réelles) |
|---|---|---|
| **BTC** | **Passer à +10** | Cycle 10 : +10 atteint à 23:05 (gross 10,42), max 14,06 sans +15 ; net ~+6 réalisables vs cycle ouvert négatif aujourd'hui |
| **XRP** | **+10 ou +15 équivalent** | Cycle 60 : max gross 5,10 — seuil plus bas sans effet pour l'instant |
| **SOL** | **Garder +5** | +16,7 USD net cette nuit sur triggers rapides |

## Viabilité théorique (5000 / 20 / 0,40 % / BNB)

| Seuil | Grilles nécessaires | Net au trigger | Frais cumulés au trigger |
|---:|---:|---:|---:|
| +10 | 16 | 8.12 | 7.88 |
| +15 | 24 | 13.12 | 10.88 |

## Fichiers

- Preuve JSON : `docs/proofs/m_trigger_10_vs_15_simulation.json`
- Script : `scripts/m_simulate_trigger_10_vs_15.py`
