"""Сборка статической страницы из обезличенных данных в data/."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import metrics as M
from .benchmark import load_benchmark
from .config import Config, Paths
from .i18n import TEXTS
from .ingest import load_returns

FALLBACK_COLORS = ["#8b5cf6", "#14b8a6", "#ec4899", "#84cc16", "#06b6d4", "#f97316", "#a3a3a3"]


class PrivacyError(RuntimeError):
    pass


# ----------------------------- форматирование -----------------------------

def fmt_pct(x, digits: int = 1, sign: bool = True) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    v = x * 100
    if abs(v) < 0.5 * 10 ** (-digits):
        v = 0.0
    s = f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"
    return s.replace("-", "−")


def fmt_ratio(x, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:+.{digits}f}".replace("-", "−")


def heat(x) -> str:
    """Фон ячейки тепловой карты: насыщенность по величине месячной доходности."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    a = 0.08 + 0.5 * min(abs(x) / 0.08, 1.0)
    rgb = "47,210,122" if x >= 0 else "255,84,104"
    return f"background:rgba({rgb},{a:.2f});color:#eceef1"


def tone(x, invert: bool = False) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)) or abs(x) < 1e-12:
        return "neu"
    pos = x > 0
    if invert:
        pos = not pos
    return "pos" if pos else "neg"


# ------------------------------- данные ----------------------------------

def _load_backtest(paths: Paths, dates: pd.DatetimeIndex) -> pd.Series:
    """data/backtest.csv из RealTest: колонка с датой и колонка Equity (или последняя числовая)."""
    if not paths.backtest.exists() or len(dates) == 0:
        return pd.Series(dtype=float)
    df = pd.read_csv(paths.backtest)
    date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
    eq_col = next((c for c in df.columns if "equity" in c.lower()), None)
    if eq_col is None:
        num_cols = [c for c in df.columns if c != date_col and pd.api.types.is_numeric_dtype(df[c])]
        if not num_cols:
            return pd.Series(dtype=float)
        eq_col = num_cols[-1]
    s = pd.Series(df[eq_col].astype(float).values, index=pd.to_datetime(df[date_col], errors="coerce"))
    s = s[s.index.notna()].sort_index()
    s = s[~s.index.duplicated(keep="last")]
    idx = s.index.union(dates)
    s = s.reindex(idx).ffill().reindex(dates).dropna()
    if s.empty or s.iloc[0] == 0:
        return pd.Series(dtype=float)
    return s / s.iloc[0]


def _round_list(values, digits: int = 5):
    return [None if pd.isna(v) else round(float(v), digits) for v in values]


def build_context(cfg: Config, paths: Paths) -> dict:
    t = TEXTS[cfg.lang]
    rets = load_returns(paths)
    if rets.empty:
        raise RuntimeError("Нет данных: сначала выполните import или update")

    E = M.equity_index(rets)
    dates = E.index
    bench_prices = load_benchmark(paths)
    brets = M.align_benchmark(bench_prices, dates)
    has_bench = len(brets) > 1
    BE = M.equity_index(brets) if has_bench else pd.Series(dtype=float)

    pm = M.series_metrics(rets, cfg.risk_free_rate)
    bm = M.series_metrics(brets, cfg.risk_free_rate) if has_bench else {}

    # Годовая и месячная доходность
    yr_p = M.calendar_returns(rets, "Y")
    yr_b = M.calendar_returns(brets, "Y") if has_bench else pd.Series(dtype=float)
    current_year = dates[-1].year
    annual = []
    for y in sorted(yr_p.index, reverse=True):
        p = float(yr_p[y])
        b = float(yr_b[y]) if y in yr_b.index else None
        annual.append({"year": int(y), "partial": y == current_year, "p": p, "b": b,
                       "x": (p - b) if b is not None else None})

    mo = M.calendar_returns(rets, "M")
    monthly = {}
    for per, v in mo.items():
        monthly.setdefault(per.year, [None] * 12)[per.month - 1] = float(v)
    monthly_rows = [{"year": y, "months": monthly[y], "total": float(yr_p[y])} for y in sorted(monthly, reverse=True)]

    # Снимок позиций
    snap = json.loads(paths.snapshot.read_text(encoding="utf-8")) if paths.snapshot.exists() else {}
    markets = snap.get("markets", {}) or {}
    colors, fi = [], 0
    for m in markets:
        c = cfg.market_colors.get(m)
        if not c:
            c = FALLBACK_COLORS[fi % len(FALLBACK_COLORS)]
            fi += 1
        colors.append(c)

    # Сделки
    trades = pd.read_csv(paths.trades, dtype={"trade_id": str}) if paths.trades.exists() else pd.DataFrame()
    tstats = M.trade_stats(trades)
    recent = []
    if len(trades):
        tr = trades.sort_values(["date", "trade_id"], ascending=False).head(cfg.trades_limit)
        for _, r in tr.iterrows():
            recent.append({"date": r["date"], "symbol": r["symbol"], "market": r["market"],
                           "side": r["side"], "pnl": float(r["pnl_pct"]) if abs(r["pnl_pct"]) > 0 else None})

    # Ряды для графиков
    backtest = _load_backtest(paths, dates) if cfg.show_backtest else pd.Series(dtype=float)
    chart = {
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "port": _round_list(E.values),
        "bench": _round_list(BE.reindex(dates).values) if has_bench else [],
        "backtest": _round_list(backtest.reindex(dates).values) if len(backtest) else [],
        "markets": {"labels": list(markets.keys()), "values": [round(v * 100, 2) for v in markets.values()],
                    "colors": colors},
    }

    # Время обновления (в ET, как принято на американском рынке)
    meta = json.loads(paths.meta.read_text()) if paths.meta.exists() else {}
    upd = meta.get("updated_utc")
    upd_dt = datetime.fromisoformat(upd.replace("Z", "+00:00")) if upd else datetime.now(timezone.utc)
    upd_et = upd_dt.astimezone(ZoneInfo("America/New_York"))
    months_full_en = upd_et.strftime("%b %d, %Y at %I:%M %p")
    updated_str = (f"{upd_et.day} {t['months'][upd_et.month - 1].lower()} {upd_et.year}, {upd_et:%H:%M}"
                   if cfg.lang == "ru" else months_full_en)

    about = cfg.about_text.strip() or t["about_default"].format(bench=cfg.benchmark)

    metric_rows = [
        ("section", t["returns"]),
        ("pct", "wtd"), ("pct", "mtd"), ("pct", "qtd"), ("pct", "ytd"), ("pct", "y1"),
        ("pct", "cagr3"), ("pct", "cagr5"),
        ("section", t["risk"]),
        ("ratio", "sharpe"), ("vol", "vol"), ("ratio", "sortino"), ("ratio", "calmar"),
        ("section", t["drawdowns"]),
        ("pct", "dd_current"), ("days", "dd_days"), ("pct", "mdd_qtd"), ("pct", "mdd_ytd"), ("pct", "mdd_all"),
    ]

    def first_date(d):
        return f"{d.day} {t['months'][d.month - 1].lower()} {d.year}" if cfg.lang == "ru" else d.strftime("%b %d, %Y")

    return {
        "t": t, "cfg": cfg, "pm": pm, "bm": bm, "has_bench": has_bench,
        "metric_rows": metric_rows, "annual": annual, "monthly_rows": monthly_rows,
        "snap": snap, "tstats": tstats, "recent": recent,
        "chart_json": json.dumps(chart, separators=(",", ":")),
        "texts_json": json.dumps({"ranges": t["ranges"], "portfolio": t["portfolio"], "bench": cfg.benchmark,
                                  "backtest": t["backtest"]}, ensure_ascii=False),
        "has_backtest": len(backtest) > 0,
        "markets_list": [{"name": m, "share": v, "color": c} for (m, v), c in zip(markets.items(), colors)],
        "initials": "".join(w[0] for w in cfg.brand_name.split()[:2]).upper() or "SP",
        "updated_str": updated_str,
        "inception_str": first_date(dates[0]),
        "asof_str": first_date(dates[-1]),
        "about_paragraphs": [p.strip() for p in about.split("\n") if p.strip()],
    }


def render_site(cfg: Config, paths: Paths, forbidden: set[str] | None = None) -> str:
    env = Environment(loader=FileSystemLoader(str(paths.templates)), autoescape=select_autoescape(["html", "j2"]))
    env.filters.update(pct=fmt_pct, ratio=fmt_ratio, tone=tone, heat=heat)
    ctx = build_context(cfg, paths)
    ctx["chartjs"] = (paths.static / "chart.umd.min.js").read_text(encoding="utf-8")
    html = env.get_template("index.html.j2").render(**ctx)

    privacy_check([html], forbidden)
    for f in (paths.returns, paths.trades, paths.snapshot, paths.meta):
        if f.exists():
            privacy_check([f.read_text(encoding="utf-8")], forbidden, where=f.name)

    paths.site.mkdir(parents=True, exist_ok=True)
    out = paths.site / "index.html"
    out.write_text(html, encoding="utf-8")
    (paths.site / ".nojekyll").write_text("")
    return str(out)


def privacy_check(texts: list[str], forbidden: set[str] | None, where: str = "index.html") -> None:
    """Номер счёта не должен попасть ни на сайт, ни в публикуемые файлы данных."""
    for s in forbidden or set():
        if s and len(s) >= 4 and any(s in txt for txt in texts):
            raise PrivacyError(f"В {where} найден номер счёта — публикация остановлена")
