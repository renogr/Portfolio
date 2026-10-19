"""Разбор Flex XML и обновление обезличенного хранилища в data/.

В data/ сохраняется только то, по чему нельзя восстановить размер или номер счёта:
  returns.csv   — дневная доходность (TWR), без сумм
  trades.csv    — сделки: дата, тикер, сторона, рынок, результат в % от капитала
  snapshot.json — текущие позиции: только веса в % и нереализованный результат в %
Суммы в долларах и номер счёта в хранилище и на сайт не попадают.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import Config, Paths

DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d-%b-%y")


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    s = value.strip().split(";")[0].split(" ")[0].split("T")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def num(value: str | None) -> float:
    if value in (None, "", "--"):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


@dataclass
class Statement:
    account_ids: set = field(default_factory=set)
    nav: pd.DataFrame = None          # index=date, columns: total, cash
    flows: pd.Series = None           # index=date, сумма вводов(+)/выводов(-) в базовой валюте
    positions: list = field(default_factory=list)
    positions_date: date | None = None
    trades: list = field(default_factory=list)


def parse_flex(xml_bytes: bytes) -> Statement:
    root = ET.fromstring(xml_bytes)
    st = Statement()

    for s in root.iter("FlexStatement"):
        if s.get("accountId"):
            st.account_ids.add(s.get("accountId"))

    # --- NAV по дням (раздел Net Asset Value in Base) ---
    nav_rows: dict[date, list[float]] = {}
    for el in root.iter("EquitySummaryByReportDateInBase"):
        d = parse_date(el.get("reportDate"))
        if d is None:
            continue
        row = nav_rows.setdefault(d, [0.0, 0.0])
        row[0] += num(el.get("total"))
        row[1] += num(el.get("cash"))
    nav = pd.DataFrame.from_dict(nav_rows, orient="index", columns=["total", "cash"]).sort_index()
    nav.index = pd.to_datetime(nav.index)
    st.nav = nav

    # --- Вводы/выводы средств (раздел Cash Transactions) ---
    flows: dict[date, float] = {}
    for el in root.iter("CashTransaction"):
        if (el.get("levelOfDetail") or "").upper() == "SUMMARY":
            continue
        if (el.get("currency") or "").upper() == "BASE_SUMMARY":
            continue
        ttype = (el.get("type") or "").lower()
        if "deposit" not in ttype and "withdraw" not in ttype:
            continue
        d = parse_date(el.get("reportDate")) or parse_date(el.get("dateTime")) or parse_date(el.get("settleDate"))
        if d is None:
            continue
        fx = num(el.get("fxRateToBase")) or 1.0
        flows[d] = flows.get(d, 0.0) + num(el.get("amount")) * fx
    fl = pd.Series(flows, dtype=float).sort_index()
    fl.index = pd.to_datetime(fl.index)
    st.flows = fl

    # --- Открытые позиции ---
    pos_dates = []
    for el in root.iter("OpenPosition"):
        lvl = (el.get("levelOfDetail") or "SUMMARY").upper()
        if lvl not in ("SUMMARY", ""):
            continue
        fx = num(el.get("fxRateToBase")) or 1.0
        value_base = num(el.get("positionValue")) * fx
        qty = num(el.get("position"))
        side = (el.get("side") or "").capitalize() or ("Short" if qty < 0 or value_base < 0 else "Long")
        if side == "Short" and value_base > 0:
            value_base = -value_base
        d = parse_date(el.get("reportDate"))
        if d:
            pos_dates.append(d)
        st.positions.append({
            "symbol": el.get("symbol") or el.get("underlyingSymbol") or "?",
            "description": el.get("description") or "",
            "asset": el.get("assetCategory") or "",
            "exchange": el.get("listingExchange") or "",
            "currency": el.get("currency") or "",
            "value_base": value_base,
            "unrealized_base": num(el.get("fifoPnlUnrealized")) * fx,
            "cost_base": abs(num(el.get("costBasisMoney"))) * fx,
            "report_date": d,
        })
    st.positions_date = max(pos_dates) if pos_dates else (nav.index.max().date() if len(nav) else None)
    if pos_dates:  # только самый свежий снимок позиций
        st.positions = [p for p in st.positions if p["report_date"] in (None, st.positions_date)]

    # --- Сделки (раздел Trades, уровень Execution) ---
    for i, el in enumerate(root.iter("Trade")):
        lvl = (el.get("levelOfDetail") or "EXECUTION").upper()
        if lvl not in ("EXECUTION", ""):
            continue
        d = parse_date(el.get("tradeDate")) or parse_date(el.get("dateTime"))
        if d is None:
            continue
        fx = num(el.get("fxRateToBase")) or 1.0
        trade_id = el.get("tradeID") or el.get("transactionID") or f"{d:%Y%m%d}-{el.get('symbol')}-{i}"
        st.trades.append({
            "trade_id": str(trade_id),
            "date": d,
            "symbol": el.get("symbol") or "?",
            "side": (el.get("buySell") or "").upper().replace("SELL (Ca.)", "SELL"),
            "asset": el.get("assetCategory") or "",
            "exchange": el.get("listingExchange") or el.get("exchange") or "",
            "currency": el.get("currency") or "",
            "open_close": (el.get("openCloseIndicator") or "").upper(),
            "realized_base": num(el.get("fifoPnlRealized")) * fx,
        })
    return st


# ---------------------------------------------------------------------------
#  Доходность по дням
# ---------------------------------------------------------------------------

def nav_to_returns(nav: pd.DataFrame, flows: pd.Series) -> tuple[pd.Series, pd.Timestamp | None]:
    """Дневная доходность (модифицированный Дитц внутри дня, поток в середине дня).
    Возвращает (доходность по дням начиная со второго дня окна, первая дата окна)."""
    nav = nav[nav["total"] > 0].sort_index()
    if len(nav) < 2:
        return pd.Series(dtype=float), (nav.index[0] if len(nav) else None)

    # Поток в нерабочий день относим к следующей дате с NAV
    flow_on = pd.Series(0.0, index=nav.index)
    for d, amt in flows.items():
        later = nav.index[nav.index >= d]
        if len(later):
            flow_on[later[0]] += amt

    totals = nav["total"]
    prev = totals.shift(1)
    f = flow_on
    rets = (totals - prev - f) / (prev + 0.5 * f)
    rets = rets.iloc[1:]
    return rets.astype(float), nav.index[0]


def load_returns(paths: Paths) -> pd.Series:
    if not paths.returns.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(paths.returns, parse_dates=["date"])
    return df.set_index("date")["ret"].astype(float).sort_index()


def save_returns(paths: Paths, s: pd.Series) -> None:
    df = pd.DataFrame({"date": s.index.strftime("%Y-%m-%d"), "ret": s.values.round(10)})
    df.to_csv(paths.returns, index=False)


def merge_returns(stored: pd.Series, fresh: pd.Series, window_start: pd.Timestamp | None) -> pd.Series:
    if stored.empty:
        base = pd.Series({window_start: 0.0}) if window_start is not None else pd.Series(dtype=float)
        merged = pd.concat([base, fresh])
    else:
        merged = fresh.combine_first(stored)
        # если новое окно начинается раньше всей истории — добавить точку старта
        if window_start is not None and window_start < stored.index.min() and window_start not in merged.index:
            merged.loc[window_start] = 0.0
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    return merged


# ---------------------------------------------------------------------------
#  Обновление хранилища
# ---------------------------------------------------------------------------

def _nav_at(nav: pd.DataFrame, d) -> float:
    ts = pd.Timestamp(d)
    sub = nav[nav.index <= ts]
    if len(sub) and sub["total"].iloc[-1] > 0:
        return float(sub["total"].iloc[-1])
    pos = nav[nav["total"] > 0]
    return float(pos["total"].iloc[0]) if len(pos) else 0.0


def update_store(statements: list[Statement], cfg: Config, paths: Paths) -> dict:
    paths.data.mkdir(parents=True, exist_ok=True)
    statements = sorted(statements, key=lambda s: s.nav.index.max() if len(s.nav) else pd.Timestamp.min)
    account_ids: set = set()

    # 1) Доходность
    stored = load_returns(paths)
    for st in statements:
        account_ids |= st.account_ids
        fresh, start = nav_to_returns(st.nav, st.flows)
        if not stored.empty and len(fresh) and fresh.index.min() > stored.index.max() + pd.Timedelta(days=7):
            print(f"Внимание: разрыв в истории между {stored.index.max():%Y-%m-%d} и {fresh.index.min():%Y-%m-%d}")
        stored = merge_returns(stored, fresh, start)
    save_returns(paths, stored)

    # 2) Сделки (результат в % от капитала на дату сделки)
    old = pd.read_csv(paths.trades, dtype={"trade_id": str}) if paths.trades.exists() else pd.DataFrame()
    rows = []
    for st in statements:
        for t in st.trades:
            nav_d = _nav_at(st.nav, t["date"])
            rows.append({
                "trade_id": t["trade_id"],
                "date": t["date"].strftime("%Y-%m-%d"),
                "symbol": t["symbol"],
                "side": "BUY" if t["side"].startswith("BUY") else "SELL",
                "asset": t["asset"],
                "market": cfg.market_for(t["exchange"], t["currency"]),
                "open_close": t["open_close"],
                "pnl_pct": round(t["realized_base"] / nav_d, 8) if nav_d else 0.0,
            })
    new = pd.DataFrame(rows)
    trades = pd.concat([old, new], ignore_index=True) if len(old) else new
    if len(trades):
        trades = trades.drop_duplicates("trade_id", keep="last").sort_values(["date", "trade_id"])
        trades.to_csv(paths.trades, index=False)

    # 3) Снимок позиций — из самого свежего отчёта
    latest = statements[-1]
    nav_last = float(latest.nav["total"].iloc[-1]) if len(latest.nav) else 0.0
    cash_last = float(latest.nav["cash"].iloc[-1]) if len(latest.nav) else 0.0
    snapshot = build_snapshot(latest, nav_last, cash_last, cfg)
    paths.snapshot.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")

    # 4) Мета (без номера счёта)
    meta = json.loads(paths.meta.read_text()) if paths.meta.exists() else {}
    meta["updated_utc"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    meta["as_of"] = stored.index.max().strftime("%Y-%m-%d") if len(stored) else None
    meta["inception"] = stored.index.min().strftime("%Y-%m-%d") if len(stored) else None
    paths.meta.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    return {"account_ids": account_ids, "days": len(stored), "trades": len(trades) if len(trades) else 0}


def build_snapshot(st: Statement, nav: float, cash: float, cfg: Config) -> dict:
    if nav <= 0:
        return {"as_of": None, "positions": []}
    agg: dict[str, dict] = {}
    for p in st.positions:
        key = p["symbol"]
        a = agg.setdefault(key, {
            "symbol": p["symbol"], "asset": p["asset"],
            "market": cfg.market_for(p["exchange"], p["currency"]),
            "value": 0.0, "unreal": 0.0, "cost": 0.0,
        })
        a["value"] += p["value_base"]
        a["unreal"] += p["unrealized_base"]
        a["cost"] += p["cost_base"]

    positions = []
    long_v = short_v = 0.0
    by_market: dict[str, float] = {}
    for a in agg.values():
        if abs(a["value"]) < 1e-9:
            continue
        w = a["value"] / nav
        if w >= 0:
            long_v += w
        else:
            short_v += -w
        by_market[a["market"]] = by_market.get(a["market"], 0.0) + abs(w)
        positions.append({
            "symbol": a["symbol"], "asset": a["asset"], "market": a["market"],
            "side": "L" if w >= 0 else "S",
            "weight": round(w, 6),
            "unrealized_pct": round(a["unreal"] / a["cost"], 6) if a["cost"] else None,
        })
    positions.sort(key=lambda x: -abs(x["weight"]))
    gross = long_v + short_v
    cash_pct = cash / nav
    margin = max(0.0, -cash_pct) if short_v == 0 else max(0.0, gross - 1.0)
    market_share = {m: round(v / gross, 6) for m, v in sorted(by_market.items(), key=lambda kv: -kv[1])} if gross else {}
    return {
        "as_of": st.positions_date.strftime("%Y-%m-%d") if st.positions_date else None,
        "gross": round(gross, 6), "net": round(long_v - short_v, 6),
        "long": round(long_v, 6), "short": round(short_v, 6),
        "cash": round(max(cash_pct, 0.0), 6), "margin": round(margin, 6),
        "n_positions": len(positions),
        "markets": market_share,
        "positions": positions,
    }


def ingest_files(files: list[Path], cfg: Config, paths: Paths) -> dict:
    statements = [parse_flex(Path(f).read_bytes()) for f in files]
    return update_store(statements, cfg, paths)


def ingest_bytes(blobs: list[bytes], cfg: Config, paths: Paths) -> dict:
    return update_store([parse_flex(b) for b in blobs], cfg, paths)
