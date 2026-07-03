# UltiumGrid_07_2026

Projet de developpement — juillet 2026.

Compte GitHub: ubntexd

## Connexion testnets

1. Copier .env.example vers .env et renseigner les cles.

2. Installer les dependances:

`ash
python3 -m pip install -r requirements.txt
`

3. Tester:

`ash
python3 scripts/connect_binance_futures_testnet.py
python3 scripts/connect_hyperliquid_testnet.py
python3 scripts/connect_all_testnets.py
`

### Endpoints

- Binance Futures Testnet: https://testnet.binancefuture.com
- Hyperliquid Testnet: https://api.hyperliquid-testnet.xyz
