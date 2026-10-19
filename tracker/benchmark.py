"""Загрузка цен бенчмарка (по умолчанию SPY) с учётом дивидендов."""
from __future__ import annotations

import io

import pandas as pd
import requests

from .config import Paths


def load_benchmark(paths: Paths) -> pd.Series:
    if not paths.benchmark.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(paths.benchmark, parse_dates=["date"])
    return df.set_index("date")["close"].astype(float).sort_index()


def _from_yahoo(ticker: str, start: str) -> pd.Series:
    import yfinance as yf

    df = yf.download(ticker, start=start, auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    close = df["Close"]
    if isinstance(close, pd.DataFrame):  # новые версии yfinance отдают MultiIndex
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close.dropna().astype(float)


def _from_stooq(ticker: str, start: str) -> pd.Series:
    # Запасной источник: цены без поправки на дивиденды (небольшая погрешность для SPY ~1.3%/год)
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"
    r = requests.get(url, timeout=60, headers={"User-Agent": "portfolio-tracker/1.0"})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"])
    s = df.set_index("Date")["Close"].astype(float)
    return s[s.index >= pd.Timestamp(start)]


def update_benchmark(ticker: str, start: str, paths: Paths) -> pd.Series:
    start = (pd.Timestamp(start) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    fresh = pd.Series(dtype=float)
    for fn in (_from_yahoo, _from_stooq):
        try:
            fresh = fn(ticker, start)
            if len(fresh):
                break
        except Exception as e:  # noqa: BLE001
            print(f"Бенчмарк: источник {fn.__name__} недоступен ({type(e).__name__})")
    if fresh.empty:
        print("Бенчмарк не обновлён, используется сохранённый")
        return load_benchmark(paths)
    # Полностью заменяем ряд: при скорректированных ценах старые значения тоже меняются
    pd.DataFrame({"date": fresh.index.strftime("%Y-%m-%d"), "close": fresh.values.round(6)}) \
        .to_csv(paths.benchmark, index=False)
    return fresh
