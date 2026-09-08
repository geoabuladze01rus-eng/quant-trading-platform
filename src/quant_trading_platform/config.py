from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import Field
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
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", allow_inf_nan=False
    )

    app_env: str = "development"
    log_level: str = "INFO"
    trading_mode: TradingMode = TradingMode.PAPER
    market_scope: MarketScope = MarketScope.MIXED
    live_trading_enabled: bool = False

    max_daily_loss_pct: Decimal = Field(default=Decimal("2"), gt=0, le=2)
    max_trade_notional_usd: Decimal = Field(default=Decimal("100"), gt=0)
    min_expected_net_pct: Decimal = Field(default=Decimal("0.10"), gt=0)
    max_market_data_age_ms: int = Field(default=1_000, gt=0)
    public_market_data_enabled: bool = True
    market_data_symbol: str = "BTC/USDT"
    market_data_poll_interval_seconds: float = Field(default=1.0, ge=0.25, le=60)
    live_order_acceptance_gate: bool = False

    paper_database_path: Path = Path("data/paper_alpha.sqlite3")
    paper_account_id: str = Field(default="paper-default", min_length=1, max_length=128)
    paper_initial_usdt: Decimal = Field(default=Decimal("100000"), ge=0)
    paper_initial_btc: Decimal = Field(default=Decimal("1"), ge=0)
    paper_initial_eth: Decimal = Field(default=Decimal("10"), ge=0)
    paper_algorithm_version: str = Field(default="paper-alpha-v1", min_length=1, max_length=64)

    binance_api_key: str | None = Field(default=None, repr=False, exclude=True)
    binance_api_secret: str | None = Field(default=None, repr=False, exclude=True)
    bybit_api_key: str | None = Field(default=None, repr=False, exclude=True)
    bybit_api_secret: str | None = Field(default=None, repr=False, exclude=True)
    okx_api_key: str | None = Field(default=None, repr=False, exclude=True)
    okx_api_secret: str | None = Field(default=None, repr=False, exclude=True)
    okx_api_passphrase: str | None = Field(default=None, repr=False, exclude=True)

    t_invest_api_token: str | None = Field(default=None, repr=False, exclude=True)
    t_invest_account_id: str | None = None
    t_invest_sandbox: bool = True

    telegram_bot_token: str | None = Field(default=None, repr=False, exclude=True)
    telegram_chat_id: str | None = None
