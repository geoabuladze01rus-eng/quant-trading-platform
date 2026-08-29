# Architecture

The platform has two isolated market contours: crypto (Binance, Bybit, OKX) and Russian instruments through T-Invest. `MARKET_SCOPE=mixed` permits one dashboard, but it does not combine credentials, balances, or order routing.

`connectors` provides read interfaces and deliberately guarded `place_order` methods. `market_data` normalizes feeds, `strategies` produces opportunities, and `risk` is the single approval boundary. `paper_trading` is the only execution target planned for the MVP. `audit_log` records signals and decisions. FastAPI exposes read-only dashboard data.

There is no live execution implementation in this repository.
