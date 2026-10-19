"""Командная строка трекера.

  python -m tracker update            скачать свежий отчёт из IBKR, обновить данные и сайт
  python -m tracker import f1.xml ...  загрузить выгруженные вручную Flex XML (история старше 365 дней)
  python -m tracker build             пересобрать сайт из сохранённых данных
  python -m tracker demo              собрать демо-сайт на синтетических данных (demo/)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import ROOT, Paths, load_config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tracker")
    ap.add_argument("command", choices=["update", "import", "build", "demo"])
    ap.add_argument("files", nargs="*", help="Flex XML для команды import")
    ap.add_argument("--data", default=str(ROOT / "data"), help="папка данных (по умолчанию data/)")
    ap.add_argument("--site", default=str(ROOT / "site"), help="папка готового сайта (по умолчанию site/)")
    ap.add_argument("--no-benchmark", action="store_true", help="не скачивать цены бенчмарка")
    args = ap.parse_args(argv)

    cfg = load_config()
    paths = Paths(data=Path(args.data), site=Path(args.site))

    from .benchmark import update_benchmark
    from .ingest import ingest_bytes, ingest_files
    from .site import render_site

    forbidden: set[str] = set()

    if args.command == "demo":
        from .demo import FAKE_ACCOUNT, make_demo
        demo_root = ROOT / "demo"
        paths = Paths(data=demo_root / "data", site=demo_root / "site")
        for f in paths.data.glob("*"):
            if f.name not in ("benchmark.csv", "backtest.csv"):
                f.unlink()
        files = make_demo(demo_root / "xml", paths.data)
        info = ingest_files(files, cfg, paths)
        forbidden = info["account_ids"] | {FAKE_ACCOUNT}
        out = render_site(cfg, paths, forbidden)
        print(f"Демо готово: {out} (дней: {info['days']}, сделок: {info['trades']})")
        return 0

    if args.command == "import":
        if not args.files:
            ap.error("укажите один или несколько Flex XML")
        info = ingest_files([Path(f) for f in args.files], cfg, paths)
        forbidden = info["account_ids"]
        print(f"Загружено: дней доходности {info['days']}, сделок {info['trades']}")

    elif args.command == "update":
        from .flex import fetch_statement
        xml = fetch_statement(os.environ.get("IBKR_FLEX_TOKEN", ""), os.environ.get("IBKR_FLEX_QUERY_ID", ""))
        info = ingest_bytes([xml], cfg, paths)
        forbidden = info["account_ids"]
        print(f"Отчёт IBKR обработан: дней доходности {info['days']}, сделок {info['trades']}")

    if args.command in ("update", "import") and not args.no_benchmark:
        from .ingest import load_returns
        rets = load_returns(paths)
        if len(rets):
            update_benchmark(cfg.benchmark, rets.index.min().strftime("%Y-%m-%d"), paths)

    out = render_site(cfg, paths, forbidden)
    print(f"Сайт собран: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
