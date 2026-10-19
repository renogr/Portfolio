"""Синтетические Flex XML для проверки всей цепочки без доступа к IBKR.

Генерирует три перекрывающихся окна по ~365 дней (как реальная выгрузка Flex),
ввод средств, позиции на трёх рынках, сделки, а также ряды бенчмарка и бэктеста.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np
import pandas as pd

FAKE_ACCOUNT = "U9999999"

UNIVERSE = [
    ("NVDA", "NASDAQ", "USD", 1.0), ("AAPL", "NASDAQ", "USD", 1.0), ("JPM", "NYSE", "USD", 1.0),
    ("XOM", "NYSE", "USD", 1.0), ("SHOP", "TSE", "CAD", 0.73), ("RY", "TSE", "CAD", 0.73),
    ("CNQ", "TSE", "CAD", 0.73), ("BHP", "ASX", "AUD", 0.66), ("CBA", "ASX", "AUD", 0.66),
    ("WES", "ASX", "AUD", 0.66), ("CSL", "ASX", "AUD", 0.66), ("TD", "TSE", "CAD", 0.73),
]


def _attrs(d: dict) -> str:
    return " ".join(f"{k}={quoteattr(str(v))}" for k, v in d.items())


def make_demo(xml_dir: Path, data_dir: Path, seed: int = 11) -> list[Path]:
    rng = np.random.default_rng(seed)
    xml_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    dates = pd.bdate_range("2024-01-02", "2026-10-16")
    n = len(dates)
    market = rng.normal(0.00045, 0.0105, n)
    alpha = np.where(dates < "2025-01-01", 0.0003, np.where(dates < "2026-01-01", 0.0017, 0.0004))
    port_r = alpha + 0.8 * market + rng.normal(0, 0.0075, n)
    port_r[-6:] = [-0.012, -0.009, -0.014, -0.006, -0.021, 0.004]

    deposits = {pd.Timestamp("2025-03-03"): 25_000.0}
    nav = np.empty(n)
    nav[0] = 100_000.0
    for i in range(1, n):
        nav[i] = nav[i - 1] * (1 + port_r[i]) + deposits.get(dates[i], 0.0)

    # Бенчмарк и бэктест (в демо-папку данных; в реальной работе SPY скачивается автоматически)
    spy = 470 * np.cumprod(1 + market)
    pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": spy.round(4)}).to_csv(data_dir / "benchmark.csv", index=False)
    bt = 100_000 * np.cumprod(1 + port_r + rng.normal(0.00025, 0.0012, n))
    pd.DataFrame({"Date": dates.strftime("%m/%d/%Y"), "Equity": bt.round(2)}).to_csv(data_dir / "backtest.csv", index=False)

    # Сделки
    trades = []
    tid = 100000
    for i in range(5, n, 3):
        sym, ex, cur, fx = UNIVERSE[rng.integers(len(UNIVERSE))]
        tid += 1
        is_close = rng.random() < 0.5
        pnl = float(rng.normal(180, 900)) if is_close else 0.0
        trades.append({"date": dates[i], "attrs": {
            "accountId": FAKE_ACCOUNT, "currency": cur, "fxRateToBase": fx, "assetCategory": "STK",
            "symbol": sym, "listingExchange": ex, "tradeID": tid, "tradeDate": dates[i].strftime("%Y%m%d"),
            "dateTime": dates[i].strftime("%Y%m%d;153000"), "buySell": "SELL" if is_close else "BUY",
            "quantity": int(rng.integers(20, 400)) * (-1 if is_close else 1),
            "tradePrice": round(float(rng.uniform(20, 300)), 2), "fifoPnlRealized": round(pnl / fx, 2),
            "openCloseIndicator": "C" if is_close else "O", "levelOfDetail": "EXECUTION"}})

    windows = [("2024-01-02", "2025-01-10"), ("2025-01-02", "2026-01-05"), ("2025-10-17", "2026-10-16")]
    files = []
    for wi, (a, b) in enumerate(windows, 1):
        mask = (dates >= a) & (dates <= b)
        wd, wn = dates[mask], nav[mask]
        last_nav = float(wn[-1])
        parts = [f'<FlexQueryResponse queryName="tracker" type="AF"><FlexStatements count="1">',
                 f'<FlexStatement accountId="{FAKE_ACCOUNT}" fromDate="{wd[0]:%Y%m%d}" toDate="{wd[-1]:%Y%m%d}" '
                 f'period="LastNCalendarDays" whenGenerated="{wd[-1]:%Y%m%d};230000">',
                 "<EquitySummaryInBase>"]
        for d, v in zip(wd, wn):
            cash = -0.073 * v
            parts.append(f"<EquitySummaryByReportDateInBase {_attrs({'accountId': FAKE_ACCOUNT, 'currency': 'USD', 'reportDate': d.strftime('%Y%m%d'), 'cash': round(cash, 2), 'stock': round(v - cash, 2), 'total': round(v, 2)})} />")
        parts.append("</EquitySummaryInBase><CashTransactions>")
        for d, amt in deposits.items():
            if wd[0] <= d <= wd[-1]:
                parts.append(f"<CashTransaction {_attrs({'accountId': FAKE_ACCOUNT, 'currency': 'USD', 'fxRateToBase': 1, 'type': 'Deposits/Withdrawals', 'amount': amt, 'reportDate': d.strftime('%Y%m%d'), 'dateTime': d.strftime('%Y%m%d;101500'), 'levelOfDetail': 'DETAIL'})} />")
        parts.append("</CashTransactions><OpenPositions>")
        if wi == len(windows):
            weights = {"NVDA": 0.11, "AAPL": 0.09, "JPM": 0.085, "XOM": 0.073, "SHOP": 0.105, "RY": 0.1,
                       "CNQ": 0.093, "BHP": 0.12, "CBA": 0.1, "WES": 0.064, "CSL": 0.06, "TD": 0.073}
            for sym, ex, cur, fx in UNIVERSE:
                w = weights.get(sym, 0)
                if not w:
                    continue
                val_base = w * last_nav
                cost = val_base / (1 + rng.normal(0.03, 0.08))
                parts.append(f"<OpenPosition {_attrs({'accountId': FAKE_ACCOUNT, 'currency': cur, 'fxRateToBase': fx, 'assetCategory': 'STK', 'symbol': sym, 'listingExchange': ex, 'reportDate': wd[-1].strftime('%Y%m%d'), 'position': 100, 'markPrice': round(val_base / fx / 100, 2), 'positionValue': round(val_base / fx, 2), 'costBasisMoney': round(cost / fx, 2), 'fifoPnlUnrealized': round((val_base - cost) / fx, 2), 'side': 'Long', 'levelOfDetail': 'SUMMARY'})} />")
        parts.append("</OpenPositions><Trades>")
        for tr in trades:
            if wd[0] <= tr["date"] <= wd[-1]:
                parts.append(f"<Trade {_attrs(tr['attrs'])} />")
        parts.append("</Trades></FlexStatement></FlexStatements></FlexQueryResponse>")
        f = xml_dir / f"demo_window_{wi}.xml"
        f.write_text("\n".join(parts), encoding="utf-8")
        files.append(f)
    return files
