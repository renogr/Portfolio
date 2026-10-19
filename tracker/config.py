"""Загрузка настроек и путей проекта."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Paths:
    data: Path
    site: Path
    templates: Path = ROOT / "templates"
    static: Path = ROOT / "static"

    @property
    def returns(self) -> Path:
        return self.data / "returns.csv"

    @property
    def trades(self) -> Path:
        return self.data / "trades.csv"

    @property
    def snapshot(self) -> Path:
        return self.data / "snapshot.json"

    @property
    def benchmark(self) -> Path:
        return self.data / "benchmark.csv"

    @property
    def backtest(self) -> Path:
        return self.data / "backtest.csv"

    @property
    def meta(self) -> Path:
        return self.data / "meta.json"


@dataclass
class Config:
    brand_name: str = "Systematic Portfolio"
    tagline: str = ""
    lang: str = "ru"
    about_text: str = ""
    benchmark: str = "SPY"
    risk_free_rate: float = 0.0
    show_positions: bool = True
    show_trades: bool = True
    trades_limit: int = 25
    show_backtest: bool = True
    exchange_markets: dict = field(default_factory=dict)
    currency_markets: dict = field(default_factory=dict)
    market_colors: dict = field(default_factory=dict)

    def market_for(self, exchange: str | None, currency: str | None) -> str:
        ex = (exchange or "").upper().strip()
        cur = (currency or "").upper().strip()
        if ex in self.exchange_markets:
            return self.exchange_markets[ex]
        if cur in self.currency_markets:
            return self.currency_markets[cur]
        return ex or cur or "OTHER"


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.yaml"
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    known = {k: v for k, v in raw.items() if k in Config.__dataclass_fields__}
    cfg = Config(**known)
    cfg.exchange_markets = {str(k).upper(): str(v) for k, v in (cfg.exchange_markets or {}).items()}
    cfg.currency_markets = {str(k).upper(): str(v) for k, v in (cfg.currency_markets or {}).items()}
    cfg.lang = cfg.lang if cfg.lang in ("ru", "en") else "ru"
    return cfg
