from enum import StrEnum

from pydantic import Field, PositiveFloat
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class MarketScope(StrEnum):
    CRYPTO = "crypto"
    RUSSIAN_STOCKS = "russian_stocks"
    MIXED = "mixed"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    trading_mode: TradingMode = TradingMode.PAPER
    market_scope: MarketScope = MarketScope.MIXED
    live_trading_enabled: bool = False

    max_daily_loss_pct: PositiveFloat = Field(default=2.0, le=100)
    max_trade_notional_usd: PositiveFloat = 100.0

    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    bybit_api_key: str | None = None
    bybit_api_secret: str | None = None
    okx_api_key: str | None = None
    okx_api_secret: str | None = None
    okx_api_passphrase: str | None = None

    t_invest_api_token: str | None = None
    t_invest_account_id: str | None = None
    t_invest_sandbox: bool = True

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
