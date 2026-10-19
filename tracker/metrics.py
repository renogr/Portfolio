"""Расчёт метрик портфеля и бенчмарка по дневной доходности (TWR)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def equity_index(rets: pd.Series) -> pd.Series:
    """Индекс капитала, равный 1.0 в дату старта."""
    return (1.0 + rets.fillna(0.0)).cumprod()


def _at_or_before(E: pd.Series, ts: pd.Timestamp) -> float | None:
    sub = E[E.index <= ts]
    return float(sub.iloc[-1]) if len(sub) else None


def _period_base(E: pd.Series, start: pd.Timestamp, allow_partial: bool) -> tuple[float | None, pd.Timestamp | None]:
    """Значение индекса на закрытии дня перед началом периода."""
    base_ts = start - pd.Timedelta(days=1)
    v = _at_or_before(E, base_ts)
    if v is not None:
        return v, E[E.index <= base_ts].index[-1]
    if allow_partial:
        return float(E.iloc[0]), E.index[0]
    return None, None


def period_return(E: pd.Series, start: pd.Timestamp, allow_partial: bool = True) -> float | None:
    if E.empty:
        return None
    base, _ = _period_base(E, start, allow_partial)
    if base is None or base == 0:
        return None
    return float(E.iloc[-1] / base - 1.0)


def trailing_return(E: pd.Series, years: int, annualize: bool) -> float | None:
    if E.empty:
        return None
    asof = E.index[-1]
    base_ts = asof - pd.DateOffset(years=years)
    if E.index[0] > base_ts + pd.Timedelta(days=5):  # истории не хватает
        return None
    base = _at_or_before(E, base_ts) or float(E.iloc[0])
    total = E.iloc[-1] / base
    return float(total ** (1.0 / years) - 1.0) if annualize else float(total - 1.0)


def max_drawdown(E: pd.Series) -> float:
    if E.empty:
        return 0.0
    return float((E / E.cummax() - 1.0).min())


def window_max_drawdown(E: pd.Series, start: pd.Timestamp) -> float | None:
    if E.empty:
        return None
    _, base_ts = _period_base(E, start, allow_partial=True)
    sub = E[E.index >= base_ts]
    return max_drawdown(sub)


def drawdown_series(E: pd.Series) -> pd.Series:
    return E / E.cummax() - 1.0


def current_drawdown(E: pd.Series) -> tuple[float, int]:
    if E.empty:
        return 0.0, 0
    dd = drawdown_series(E)
    cur = float(dd.iloc[-1])
    if cur >= -1e-12:
        return 0.0, 0
    peak_date = E[E == E.cummax()].index[-1]
    return cur, int((E.index[-1] - peak_date).days)


def cagr_all(E: pd.Series) -> float | None:
    if len(E) < 2:
        return None
    days = (E.index[-1] - E.index[0]).days
    if days <= 0:
        return None
    return float((E.iloc[-1] / E.iloc[0]) ** (365.25 / days) - 1.0)


def risk_stats(rets: pd.Series, rf_annual: float = 0.0) -> dict:
    r = rets.dropna()
    if len(r) < 2:
        return {"sharpe": None, "vol": None, "sortino": None}
    rf_d = rf_annual / TRADING_DAYS
    ex = r - rf_d
    sd = r.std(ddof=1)
    downside = math.sqrt(float(np.mean(np.minimum(ex, 0.0) ** 2)))
    ann = math.sqrt(TRADING_DAYS)
    return {
        "sharpe": float(ex.mean() / sd * ann) if sd > 0 else None,
        "vol": float(sd * ann),
        "sortino": float(ex.mean() / downside * ann) if downside > 0 else None,
    }


def series_metrics(rets: pd.Series, rf: float = 0.0) -> dict:
    """Полный набор метрик для ряда доходности (первая точка — старт, доходность 0)."""
    E = equity_index(rets)
    if E.empty:
        return {}
    asof = E.index[-1]
    week_start = asof - pd.Timedelta(days=asof.weekday())
    month_start = asof.replace(day=1)
    q_start = pd.Timestamp(asof.year, 3 * ((asof.month - 1) // 3) + 1, 1)
    y_start = pd.Timestamp(asof.year, 1, 1)

    daily = rets.iloc[1:]
    risk = risk_stats(daily, rf)
    mdd_all = max_drawdown(E)
    c_all = cagr_all(E)
    cur_dd, dd_days = current_drawdown(E)
    return {
        "wtd": period_return(E, week_start),
        "mtd": period_return(E, month_start),
        "qtd": period_return(E, q_start),
        "ytd": period_return(E, y_start),
        "y1": trailing_return(E, 1, annualize=False),
        "cagr3": trailing_return(E, 3, annualize=True),
        "cagr5": trailing_return(E, 5, annualize=True),
        "total": float(E.iloc[-1] - 1.0),
        "cagr_all": c_all,
        **risk,
        "calmar": (c_all / abs(mdd_all)) if (c_all is not None and mdd_all < 0) else None,
        "dd_current": cur_dd,
        "dd_days": dd_days,
        "mdd_qtd": window_max_drawdown(E, q_start),
        "mdd_ytd": window_max_drawdown(E, y_start),
        "mdd_all": mdd_all,
    }


def align_benchmark(prices: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Доходность бенчмарка на те же даты, что и у портфеля (первая точка = 0)."""
    if prices.empty or len(dates) == 0:
        return pd.Series(dtype=float)
    idx = prices.index.union(dates)
    p = prices.reindex(idx).ffill().reindex(dates)
    p = p.dropna()
    if p.empty:
        return pd.Series(dtype=float)
    r = p.pct_change().fillna(0.0)
    r.iloc[0] = 0.0
    return r


def calendar_returns(rets: pd.Series, freq: str) -> pd.Series:
    """Доходность по календарным годам ('Y') или месяцам ('M')."""
    if rets.empty:
        return pd.Series(dtype=float)
    key = rets.index.year if freq == "Y" else rets.index.to_period("M")
    return (1.0 + rets).groupby(key).prod() - 1.0


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {}
    t = trades.copy()
    t["date"] = pd.to_datetime(t["date"])
    closing = t[(t["open_close"].fillna("").str.contains("C")) | (t["pnl_pct"].abs() > 0)]
    closing = closing[closing["pnl_pct"].abs() > 0]
    wins = closing[closing["pnl_pct"] > 0]["pnl_pct"]
    losses = closing[closing["pnl_pct"] < 0]["pnl_pct"]
    last = t["date"].max()
    return {
        "total": int(len(t)),
        "closed": int(len(closing)),
        "last30": int((t["date"] > last - pd.Timedelta(days=30)).sum()),
        "win_rate": float(len(wins) / len(closing)) if len(closing) else None,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None,
        "payoff": float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None,
        "avg_win": float(wins.mean()) if len(wins) else None,
        "avg_loss": float(losses.mean()) if len(losses) else None,
    }
