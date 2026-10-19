# Трекер портфеля на данных IBKR

Статический сайт с результатами системной стратегии: доходность (TWR), риск-метрики, просадки,
экспозиция, распределение по рынкам, кривая капитала против SPY, доходность по годам и месяцам, сделки.
Данные раз в день забираются из Interactive Brokers через Flex Web Service, сайт пересобирается
и публикуется на GitHub Pages. Сервер не нужен, всё бесплатно.

## Что публикуется, а что нет

На сайт и в репозиторий попадают **только проценты**: дневная доходность, веса позиций, результат сделок
в % от капитала. Номер счёта, стоимость счёта, суммы, количество акций и цены сделок не сохраняются.
Исходные XML от IBKR в репозиторий не попадают (`*.xml` в `.gitignore`), а сборка останавливается,
если в итоговых файлах обнаружится номер счёта.

## 1. Настройка Flex Query в IBKR

Войдите в Client Portal под логином счёта, результаты которого будут на сайте.

**Performance & Reports → Flex Queries → Activity Flex Query → «+»**

Название — любое, например `tracker`. Включите разделы:

| Раздел | Опции | Поля |
|---|---|---|
| Net Asset Value (NAV) in Base | — | Report Date, Cash, Total (можно Select All) |
| Open Positions | Summary | Symbol, Asset Class, Listing Exchange, Currency, FX Rate To Base, Position, Position Value, Cost Basis Money, FIFO Unrealized P/L, Side, Report Date |
| Trades | Execution | Trade ID, Trade Date, Date/Time, Symbol, Asset Class, Listing Exchange, Currency, FX Rate To Base, Buy/Sell, Open/Close Indicator, FIFO Realized P/L |
| Cash Transactions | Deposits & Withdrawals | Type, Amount, Currency, FX Rate To Base, Report Date, Date/Time |

Параметры доставки:
- Format: **XML**
- Period: **Last 365 Calendar Days**
- Date Format: **yyyyMMdd**, Time Format: **HHmmss**, разделитель даты и времени: **;**

Сохраните запрос и запишите его **Query ID** (число в списке Flex Queries).

**Токен:** Settings → Account Settings → Reporting → **Flex Web Service** → включить и сгенерировать токен.
Выберите максимальный срок действия и поставьте себе напоминание продлить токен.

> Проверка: нажмите на запросе «Run», выберите XML и убедитесь, что отчёт формируется и в нём есть
> раздел Net Asset Value с данными по дням.

## 2. История старше 365 дней (один раз)

Flex отдаёт максимум 365 дней за раз. Если стратегия работает дольше:

1. В Client Portal запустите тот же запрос вручную («Run»), выбрав Period: **Custom Date Range**,
   кусками по году (окна могут перекрываться), формат XML.
2. Положите файлы в папку проекта и выполните:
   ```bash
   pip install -r requirements.txt
   python -m tracker import 2024.xml 2025.xml 2026.xml
   ```
3. Закоммитьте папку `data/` (XML-файлы не коммитятся автоматически).

Дальше ежедневное обновление само добавляет новые дни к истории.

## 3. Публикация на GitHub Pages

1. Создайте репозиторий на GitHub (публичный: в нём только код и обезличенные проценты) и загрузите проект.
2. **Settings → Secrets and variables → Actions → New repository secret**, два секрета:
   - `IBKR_FLEX_TOKEN` — токен Flex Web Service
   - `IBKR_FLEX_QUERY_ID` — Query ID
3. **Settings → Pages → Source: GitHub Actions**.
4. **Actions → Update tracker → Run workflow** — первый запуск вручную.

Сайт появится по адресу `https://<ваш-логин>.github.io/<репозиторий>/`. Свой домен подключается там же,
в Settings → Pages → Custom domain.

Обновление идёт автоматически со вторника по субботу в 06:30 по Нью-Йорку (после каждой торговой сессии).
Расписание меняется в `.github/workflows/update.yml`.

## 4. Настройки внешнего вида

Всё в `config.yaml`: название, подпись, язык (`ru`/`en`), текст блока «О трекере», бенчмарк,
показывать ли таблицу позиций и ленту сделок, подписи и цвета рынков.

## 5. Бэктест RealTest на графике (по желанию)

Экспортируйте кривую капитала из RealTest в CSV с колонками даты и `Equity` и сохраните как
`data/backtest.csv`. На графике появится пунктирная линия бэктеста, приведённая к дате старта —
сразу видно расхождение модели и фактического исполнения. Отключается `show_backtest: false`.

## Команды

```bash
python -m tracker demo      # демо-сайт на синтетических данных: demo/site/index.html
python -m tracker update    # скачать отчёт IBKR (нужны переменные IBKR_FLEX_TOKEN и IBKR_FLEX_QUERY_ID)
python -m tracker import *.xml   # загрузить выгруженные вручную отчёты
python -m tracker build     # пересобрать сайт из data/ без загрузки
```

## Как считаются метрики

- **Доходность** — TWR по дням: `(NAV_t − NAV_{t−1} − ввод/вывод) / (NAV_{t−1} + ½·ввод/вывод)`,
  дни перемножаются. Вводы и выводы средств на результат не влияют. Комиссии и проценты по марже
  уже учтены в NAV.
- **Sharpe, Sortino, волатильность** — по дневной доходности, ×√252; безрисковая ставка задаётся в конфиге.
- **Calmar** — среднегодовая доходность с начала / |максимальная просадка|.
- **Дней в просадке** — календарные дни с последнего максимума капитала.
- **Экспозиция** — сумма |стоимость позиций| / капитал; **маржа** — отрицательный кэш / капитал.
- **SPY** — с учётом дивидендов (скорректированные цены Yahoo Finance).
- **Результат сделки** — реализованная прибыль/убыток при закрытии в % от капитала на дату сделки.
