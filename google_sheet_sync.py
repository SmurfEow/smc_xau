import argparse
import calendar
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import gspread
from gspread.exceptions import WorksheetNotFound
from google.oauth2.service_account import Credentials


LOCAL_HISTORY_URL = os.getenv("QUANTUM_HISTORY_URL", "http://127.0.0.1:8090/api/history/dashboard?period=all")
BROKER_OFFSET_HOURS = int(os.getenv("QUANTUM_BROKER_OFFSET_HOURS", "-8"))
LOG_TIME_LOCAL = os.getenv("QUANTUM_LOG_TIME_LOCAL", "19:30")
DEFAULT_SYMBOL = os.getenv("QUANTUM_SHEET_SYMBOL", "XAUUSD.m")
DEFAULT_MAGIC = int(os.getenv("QUANTUM_SHEET_MAGIC", os.getenv("AUTOTRADE_MAGIC", "20260324")) or 20260324)
DEFAULT_SOURCE = os.getenv("QUANTUM_TRADE_SOURCE", "").strip()
WEBHOOK_URL = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "").strip()
WEBHOOK_SECRET = os.getenv("GOOGLE_SHEETS_WEBHOOK_SECRET", "").strip()
DEFAULT_SERVICE_ACCOUNT_JSON = r"C:\Users\User\Downloads\xauusdregime-bda2270705f3.json"
DEFAULT_GOOGLE_SHEET_ID = "1XDNo6mnh7IAxE7mLpuGpF8jAr-sZ43gL0jiB2c6meIo"
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", DEFAULT_SERVICE_ACCOUNT_JSON).strip()
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", DEFAULT_GOOGLE_SHEET_ID).strip()
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "profit_calendar").strip() or "profit_calendar"
CALENDAR_WEEKDAY_HEADERS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


@dataclass
class DailyRow:
    date_broker: str
    log_time_local: str
    pnl: float
    trades: int
    wins: int
    losses: int
    win_pct: float
    symbol: str
    magic: int


@dataclass
class SummaryRow:
    level: str
    period: str
    pnl: float
    trades: int
    wins: int
    losses: int
    win_pct: float


def _http_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_history() -> dict:
    return _http_json(LOCAL_HISTORY_URL)


def _parse_utc_label(label: str) -> datetime:
    return datetime.strptime(label, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _round2(value: float) -> float:
    return round(float(value), 2)


def aggregate_rows(
    deals: Iterable[dict],
    log_time_local: str = LOG_TIME_LOCAL,
    symbol: str = DEFAULT_SYMBOL,
    magic: int = DEFAULT_MAGIC,
    trade_source: str = DEFAULT_SOURCE,
) -> tuple[list[DailyRow], list[SummaryRow]]:
    grouped: dict[str, list[float]] = defaultdict(list)

    for deal in deals:
        if trade_source and str(deal.get("trade_source", "")) != trade_source:
            continue
        if int(deal.get("magic", 0) or 0) != int(magic):
            continue
        close_label = str(deal.get("close_time_label") or "").strip()
        if not close_label:
            continue
        close_dt = datetime.strptime(close_label, "%Y-%m-%d %H:%M:%S")
        grouped[close_dt.strftime("%Y-%m-%d")].append(float(deal.get("net", 0.0) or 0.0))

    daily_rows: list[DailyRow] = []
    for date_broker in sorted(grouped):
        pnls = grouped[date_broker]
        trades = len(pnls)
        wins = sum(1 for pnl in pnls if pnl > 0)
        losses = sum(1 for pnl in pnls if pnl < 0)
        win_pct = _round2((wins / trades) * 100 if trades else 0.0)
        daily_rows.append(
            DailyRow(
                date_broker=date_broker,
                log_time_local=log_time_local,
                pnl=_round2(sum(pnls)),
                trades=trades,
                wins=wins,
                losses=losses,
                win_pct=win_pct,
                symbol=symbol,
                magic=magic,
            )
        )

    summary_rows: list[SummaryRow] = []

    year_groups: dict[str, list[DailyRow]] = defaultdict(list)
    month_groups: dict[str, list[DailyRow]] = defaultdict(list)
    week_groups: dict[str, list[DailyRow]] = defaultdict(list)
    for row in daily_rows:
        row_date = datetime.strptime(row.date_broker, "%Y-%m-%d")
        year_groups[row_date.strftime("%Y")].append(row)
        month_groups[row_date.strftime("%Y-%m")].append(row)
        monday = row_date - timedelta(days=((row_date.weekday()) % 7))
        week_groups[monday.strftime("%Y-%m-%d")].append(row)

    for year_key in sorted(year_groups):
        rows = year_groups[year_key]
        summary_rows.append(_summarize_rows("YEAR", year_key, rows))

    for month_key in sorted(month_groups):
        rows = month_groups[month_key]
        month_label = datetime.strptime(rows[0].date_broker, "%Y-%m-%d").strftime("%b %Y")
        summary_rows.append(_summarize_rows("MONTH", month_label, rows))

    for week_key in sorted(week_groups):
        rows = sorted(week_groups[week_key], key=lambda item: item.date_broker)
        period = f"{rows[0].date_broker} -> {rows[-1].date_broker}"
        summary_rows.append(_summarize_rows("WEEK", period, rows))

    for row in daily_rows:
        summary_rows.append(
            SummaryRow(
                level="DAY",
                period=row.date_broker,
                pnl=row.pnl,
                trades=row.trades,
                wins=row.wins,
                losses=row.losses,
                win_pct=row.win_pct,
            )
        )

    return daily_rows, summary_rows


def _summarize_rows(level: str, period: str, rows: Iterable[DailyRow]) -> SummaryRow:
    rows = list(rows)
    trades = sum(row.trades for row in rows)
    wins = sum(row.wins for row in rows)
    losses = sum(row.losses for row in rows)
    return SummaryRow(
        level=level,
        period=period,
        pnl=_round2(sum(row.pnl for row in rows)),
        trades=trades,
        wins=wins,
        losses=losses,
        win_pct=_round2((wins / trades) * 100 if trades else 0.0),
    )


def _filter_rows_by_date(daily_rows: list[DailyRow], summary_rows: list[SummaryRow], date_broker: str | None) -> tuple[list[DailyRow], list[SummaryRow]]:
    if not date_broker:
        return daily_rows, summary_rows
    filtered_daily = [row for row in daily_rows if row.date_broker == date_broker]
    filtered_summary = [row for row in summary_rows if not (row.level == "DAY" and row.period != date_broker)]
    return filtered_daily, filtered_summary


def _rows_to_tsv(rows: Iterable[dict], headers: list[str]) -> str:
    lines = ["\t".join(headers)]
    for row in rows:
        values = []
        for header in headers:
            value = row.get(header, "")
            if isinstance(value, float):
                value = f"{value:.2f}"
            values.append(str(value))
        lines.append("\t".join(values))
    return "\n".join(lines)


def build_journal_rows(daily_rows: list[DailyRow], summary_rows: list[SummaryRow], mode: str) -> list[dict]:
    journal_rows: list[dict] = []
    if mode not in ("all", "summary"):
        return journal_rows

    daily_by_date = {row.date_broker: row for row in daily_rows}

    years = sorted({row.period for row in summary_rows if row.level == "YEAR"})
    months = sorted([row for row in summary_rows if row.level == "MONTH"], key=lambda item: datetime.strptime(item.period, "%b %Y"))
    weeks = sorted(
        [row for row in summary_rows if row.level == "WEEK"],
        key=lambda item: datetime.strptime(item.period.split("->")[0].strip(), "%Y-%m-%d"),
    )

    for year in years:
        year_row = next((row for row in summary_rows if row.level == "YEAR" and row.period == year), None)
        if year_row:
            journal_rows.append(
                {
                    "level": "YEAR",
                    "period": year_row.period,
                    "pnl": year_row.pnl,
                    "trades": year_row.trades,
                    "wins": year_row.wins,
                    "losses": year_row.losses,
                    "win_pct": year_row.win_pct,
                    "log_time_local": "",
                    "symbol": "",
                    "magic": "",
                }
            )

        year_months = [row for row in months if datetime.strptime(row.period, "%b %Y").strftime("%Y") == year]
        for month_row in year_months:
            journal_rows.append(
                {
                    "level": "MONTH",
                    "period": month_row.period,
                    "pnl": month_row.pnl,
                    "trades": month_row.trades,
                    "wins": month_row.wins,
                    "losses": month_row.losses,
                    "win_pct": month_row.win_pct,
                    "log_time_local": "",
                    "symbol": "",
                    "magic": "",
                }
            )

            month_start = datetime.strptime(month_row.period, "%b %Y")
            month_key = month_start.strftime("%Y-%m")
            month_weeks = []
            for week_row in weeks:
                week_start = datetime.strptime(week_row.period.split("->")[0].strip(), "%Y-%m-%d")
                if week_start.strftime("%Y-%m") == month_key:
                    month_weeks.append(week_row)

            for week_row in month_weeks:
                journal_rows.append(
                    {
                        "level": "WEEK",
                        "period": week_row.period,
                        "pnl": week_row.pnl,
                        "trades": week_row.trades,
                        "wins": week_row.wins,
                        "losses": week_row.losses,
                        "win_pct": week_row.win_pct,
                        "log_time_local": "",
                        "symbol": "",
                        "magic": "",
                    }
                )
                week_start = datetime.strptime(week_row.period.split("->")[0].strip(), "%Y-%m-%d")
                week_end = datetime.strptime(week_row.period.split("->")[1].strip(), "%Y-%m-%d")
                cursor = week_start
                while cursor <= week_end:
                    day_key = cursor.strftime("%Y-%m-%d")
                    day_row = daily_by_date.get(day_key)
                    if day_row:
                        journal_rows.append(
                            {
                                "level": "DAY",
                                "period": day_row.date_broker,
                                "pnl": day_row.pnl,
                                "trades": day_row.trades,
                                "wins": day_row.wins,
                                "losses": day_row.losses,
                                "win_pct": day_row.win_pct,
                                "symbol": day_row.symbol,
                                "magic": str(day_row.magic),
                                "log_time_local": day_row.log_time_local,
                            }
                        )
                    cursor += timedelta(days=1)

    return journal_rows


def _get_or_create_sheet(spreadsheet: gspread.Spreadsheet, title: str, rows: int, cols: int) -> gspread.Worksheet:
    try:
        worksheet = spreadsheet.worksheet(title)
    except WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)
    if worksheet.row_count < rows:
        worksheet.add_rows(rows - worksheet.row_count)
    if worksheet.col_count < cols:
        worksheet.add_cols(cols - worksheet.col_count)
    return worksheet


def _replace_sheet(spreadsheet: gspread.Spreadsheet, title: str, rows: int, cols: int) -> gspread.Worksheet:
    try:
        existing = spreadsheet.worksheet(title)
        if len(spreadsheet.worksheets()) > 1:
            spreadsheet.del_worksheet(existing)
        else:
            if existing.row_count > rows:
                existing.resize(rows=rows, cols=max(existing.col_count, cols))
            else:
                if existing.row_count < rows:
                    existing.add_rows(rows - existing.row_count)
                if existing.col_count < cols:
                    existing.add_cols(cols - existing.col_count)
            existing.clear()
            return existing
    except WorksheetNotFound:
        pass
    return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def _month_groups(daily_rows: list[DailyRow]) -> dict[str, list[DailyRow]]:
    grouped: dict[str, list[DailyRow]] = defaultdict(list)
    for row in daily_rows:
        row_date = datetime.strptime(row.date_broker, "%Y-%m-%d")
        grouped[row_date.strftime("%b %Y")].append(row)
    return grouped


def _format_calendar_cell(row: DailyRow) -> str:
    pnl_text = f"{row.pnl:+.2f}"
    return f"{int(row.date_broker[-2:])}\nP/L {pnl_text}\nT {row.trades}\nW/L {row.wins}/{row.losses}"


def _column_letter(index_1_based: int) -> str:
    result = ""
    index = index_1_based
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _build_month_calendar_block(month_label: str, rows: list[DailyRow]) -> tuple[list[list[str]], list[tuple[int, int, dict, dict]]]:
    month_date = datetime.strptime(month_label, "%b %Y")
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdayscalendar(month_date.year, month_date.month)
    daily_map = {row.date_broker: row for row in rows}
    month_pnl = _round2(sum(row.pnl for row in rows))
    month_trades = sum(row.trades for row in rows)
    month_wins = sum(row.wins for row in rows)
    month_losses = sum(row.losses for row in rows)
    month_win_pct = _round2((month_wins / month_trades) * 100 if month_trades else 0.0)

    title_row = [[month_date.strftime("%B"), "", "", "", "", "", ""]]
    summary_labels = [["Net", "Trades", "Wins", "Losses", "Win %", "Symbol", "Magic"]]
    summary_values = [[month_pnl, month_trades, month_wins, month_losses, month_win_pct, DEFAULT_SYMBOL, str(DEFAULT_MAGIC)]]
    header_row = [CALENDAR_WEEKDAY_HEADERS]
    calendar_rows: list[list[str]] = []
    color_map: list[tuple[int, int, dict, dict]] = []

    for week_row_number, week in enumerate(weeks, start=7):
        row_values: list[str] = []
        for day_index, day in enumerate(week, start=1):
            if day == 0:
                row_values.append("")
                continue
            date_key = datetime(month_date.year, month_date.month, day).strftime("%Y-%m-%d")
            daily_row = daily_map.get(date_key)
            if daily_row:
                row_values.append(_format_calendar_cell(daily_row))
                if daily_row.pnl > 0:
                    bg = {"red": 0.82, "green": 0.94, "blue": 0.86}
                elif daily_row.pnl < 0:
                    bg = {"red": 0.97, "green": 0.84, "blue": 0.84}
                else:
                    bg = {"red": 0.90, "green": 0.91, "blue": 0.93}
                color_map.append(
                    (
                        week_row_number,
                        day_index,
                        bg,
                        {"foregroundColor": {"red": 0.07, "green": 0.09, "blue": 0.12}, "fontSize": 10},
                    )
                )
            else:
                row_values.append(str(day))
                color_map.append(
                    (
                        week_row_number,
                        day_index,
                        {"red": 0.93, "green": 0.94, "blue": 0.95},
                        {"foregroundColor": {"red": 0.45, "green": 0.48, "blue": 0.52}, "fontSize": 10},
                    )
                )
        calendar_rows.append(row_values)

    all_values = title_row + [["", "", "", "", "", "", ""]] + summary_labels + summary_values + [["", "", "", "", "", "", ""]] + header_row + calendar_rows
    return all_values, color_map


def _write_year_calendar(spreadsheet: gspread.Spreadsheet, year: str, rows: list[DailyRow]) -> None:
    year_int = int(year)
    month_rows_map = _month_groups([row for row in rows if row.date_broker.startswith(f"{year}-")])

    all_values: list[list[str]] = [[year, "", "", "", "", "", ""]]
    format_ranges = [
        {
            "range": "A1:G1",
            "format": {
                "textFormat": {"bold": True, "fontSize": 18, "foregroundColor": {"red": 0.12, "green": 0.16, "blue": 0.24}},
                "horizontalAlignment": "CENTER",
                "backgroundColor": {"red": 0.93, "green": 0.95, "blue": 0.98},
            },
        }
    ]
    cell_formats: list[dict] = []
    calendar_row_ranges: list[tuple[int, int]] = []

    for month in range(1, 13):
        month_label = datetime(year_int, month, 1).strftime("%b %Y")
        month_rows = month_rows_map.get(month_label, [])
        month_values, month_colors = _build_month_calendar_block(month_label, month_rows)
        start_row = len(all_values) + 2
        all_values.extend([["", "", "", "", "", "", ""]])
        all_values.extend(month_values)
        format_ranges.extend(
            [
                {
                    "range": f"A{start_row}:G{start_row}",
                    "format": {
                        "textFormat": {"bold": True, "fontSize": 16, "foregroundColor": {"red": 0.13, "green": 0.17, "blue": 0.25}},
                        "horizontalAlignment": "CENTER",
                        "backgroundColor": {"red": 0.94, "green": 0.96, "blue": 0.99},
                    },
                },
                {
                    "range": f"A{start_row + 2}:G{start_row + 2}",
                    "format": {
                        "textFormat": {"bold": True, "fontSize": 9, "foregroundColor": {"red": 0.35, "green": 0.39, "blue": 0.46}},
                        "backgroundColor": {"red": 0.98, "green": 0.98, "blue": 0.99},
                        "horizontalAlignment": "CENTER",
                        "borders": {
                            "top": {"style": "SOLID", "color": {"red": 0.86, "green": 0.88, "blue": 0.92}},
                            "bottom": {"style": "SOLID", "color": {"red": 0.86, "green": 0.88, "blue": 0.92}},
                        },
                    },
                },
                {
                    "range": f"A{start_row + 3}:G{start_row + 3}",
                    "format": {
                        "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": {"red": 0.10, "green": 0.13, "blue": 0.19}},
                        "backgroundColor": {"red": 0.985, "green": 0.99, "blue": 1.0},
                        "horizontalAlignment": "CENTER",
                        "borders": {
                            "bottom": {"style": "SOLID", "color": {"red": 0.86, "green": 0.88, "blue": 0.92}},
                        },
                    },
                },
                {
                    "range": f"A{start_row + 5}:G{start_row + 5}",
                    "format": {
                        "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": {"red": 0.32, "green": 0.36, "blue": 0.43}},
                        "horizontalAlignment": "CENTER",
                        "backgroundColor": {"red": 0.93, "green": 0.95, "blue": 0.98},
                        "borders": {
                            "top": {"style": "SOLID", "color": {"red": 0.84, "green": 0.87, "blue": 0.92}},
                            "bottom": {"style": "SOLID", "color": {"red": 0.84, "green": 0.87, "blue": 0.92}},
                        },
                    },
                },
                {
                    "range": f"A{start_row + 6}:G{start_row + len(month_values) - 1}",
                    "format": {
                        "horizontalAlignment": "LEFT",
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "WRAP",
                        "textFormat": {"fontSize": 10, "foregroundColor": {"red": 0.10, "green": 0.13, "blue": 0.18}},
                        "borders": {
                            "top": {"style": "SOLID", "color": {"red": 0.88, "green": 0.90, "blue": 0.94}},
                            "bottom": {"style": "SOLID", "color": {"red": 0.88, "green": 0.90, "blue": 0.94}},
                            "left": {"style": "SOLID", "color": {"red": 0.88, "green": 0.90, "blue": 0.94}},
                            "right": {"style": "SOLID", "color": {"red": 0.88, "green": 0.90, "blue": 0.94}},
                        },
                    },
                },
            ]
        )
        calendar_row_ranges.append((start_row + 6, start_row + len(month_values) - 1))

        for row_number, col_number, bg, text_format in month_colors:
            actual_row = start_row + row_number - 1
            cell_formats.append(
                {
                    "range": f"{_column_letter(col_number)}{actual_row}",
                    "format": {
                        "backgroundColor": bg,
                        "wrapStrategy": "WRAP",
                        "verticalAlignment": "TOP",
                        "horizontalAlignment": "LEFT",
                        "textFormat": text_format,
                    },
                }
            )

    required_rows = max(len(all_values) + 5, 20)
    worksheet = _replace_sheet(spreadsheet, year, rows=required_rows, cols=7)

    worksheet.update(range_name=f"A1:G{len(all_values)}", values=all_values, value_input_option="USER_ENTERED")
    worksheet.batch_format(
        [
            {
                "range": f"A1:G{worksheet.row_count}",
                "format": {
                    "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                    "textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}, "fontSize": 10, "bold": False},
                    "wrapStrategy": "OVERFLOW_CELL",
                    "verticalAlignment": "MIDDLE",
                    "horizontalAlignment": "LEFT",
                },
            }
        ]
        + format_ranges
        + cell_formats
    )
    merge_requests = [
        {
            "mergeCells": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 7,
                },
                "mergeType": "MERGE_ALL",
            }
        }
    ]
    for month in range(1, 13):
        month_label = datetime(year_int, month, 1).strftime("%b %Y")
        month_rows = month_rows_map.get(month_label, [])
        month_values, _ = _build_month_calendar_block(month_label, month_rows)
        # recompute starting row for each month section
    running_row = 3
    for month in range(1, 13):
        month_label = datetime(year_int, month, 1).strftime("%b %Y")
        month_rows = month_rows_map.get(month_label, [])
        month_values, _ = _build_month_calendar_block(month_label, month_rows)
        start_row = running_row
        merge_requests.append(
            {
                "mergeCells": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": start_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7,
                    },
                    "mergeType": "MERGE_ALL",
                }
            }
        )
        running_row = start_row + len(month_values) + 1
    requests = []
    requests.extend(merge_requests)
    requests.extend([
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 7,
                },
                "properties": {"pixelSize": 170},
                "fields": "pixelSize",
            }
        },
    ])
    requests.append(
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "ROWS",
                    "startIndex": 0,
                    "endIndex": worksheet.row_count,
                },
                "properties": {"pixelSize": 26},
                "fields": "pixelSize",
            }
        }
    )
    for start_row, end_row in calendar_row_ranges:
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        "startIndex": start_row - 1,
                        "endIndex": end_row,
                    },
                    "properties": {"pixelSize": 92},
                    "fields": "pixelSize",
                }
            }
        )
    # freeze title + month summary headers
    requests.append(
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": worksheet.id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        }
    )
    spreadsheet.batch_update({"requests": requests})


def push_to_google_sheet(daily_rows: list[DailyRow], summary_rows: list[SummaryRow], mode: str) -> dict:
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set.")
    if not GOOGLE_SHEET_ID:
        raise RuntimeError("GOOGLE_SHEET_ID is not set.")
    if not os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON):
        raise RuntimeError(f"Service account JSON not found: {GOOGLE_SERVICE_ACCOUNT_JSON}")

    credentials = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON, scopes=GOOGLE_SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

    try:
        legacy_tab = spreadsheet.worksheet("performance_log")
        legacy_tab.update_title(GOOGLE_SHEET_TAB)
    except WorksheetNotFound:
        pass

    if mode in ("all", "daily"):
        years = sorted({row.date_broker[:4] for row in daily_rows})
        for year in years:
            _write_year_calendar(spreadsheet, year, daily_rows)

    return {
        "ok": True,
        "mode": mode,
        "sheet_id": GOOGLE_SHEET_ID,
        "sheet_tab": "year-calendar",
        "journal_updated": 0,
        "calendar_tabs_updated": len({row.date_broker[:4] for row in daily_rows}) if mode in ("all", "daily") else 0,
    }


def push_to_webhook(daily_rows: list[DailyRow], summary_rows: list[SummaryRow], mode: str) -> dict:
    if not WEBHOOK_URL:
        raise RuntimeError("GOOGLE_SHEETS_WEBHOOK_URL is not set.")
    payload = {
        "secret": WEBHOOK_SECRET,
        "mode": mode,
        "daily_rows": [asdict(row) for row in daily_rows],
        "summary_rows": [asdict(row) for row in summary_rows],
        "journal_rows": build_journal_rows(daily_rows, summary_rows, mode),
    }
    return _http_json(WEBHOOK_URL, method="POST", payload=payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Quantum daily trade summaries to Google Sheets.")
    parser.add_argument("--date", help="Sync a single broker date in YYYY-MM-DD format.")
    parser.add_argument("--mode", choices=["daily", "summary", "all"], default="all")
    parser.add_argument("--push", action="store_true", help="POST rows to the configured webhook instead of printing them.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of TSV.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        history = fetch_history()
        daily_rows, summary_rows = aggregate_rows(history.get("deals", []))
        daily_rows, summary_rows = _filter_rows_by_date(daily_rows, summary_rows, args.date)
        payload = {
            "daily_rows": [asdict(row) for row in daily_rows],
            "summary_rows": [asdict(row) for row in summary_rows],
        }

        if args.push:
            send_daily = [DailyRow(**row) for row in payload["daily_rows"]]
            send_summary = [SummaryRow(**row) for row in payload["summary_rows"]]
            if GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID and os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON):
                response = push_to_google_sheet(send_daily, send_summary, args.mode)
            else:
                if not WEBHOOK_URL:
                    missing = []
                    if not GOOGLE_SERVICE_ACCOUNT_JSON:
                        missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
                    elif not os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON):
                        missing.append(f"service account file missing at {GOOGLE_SERVICE_ACCOUNT_JSON}")
                    if not GOOGLE_SHEET_ID:
                        missing.append("GOOGLE_SHEET_ID")
                    if not missing:
                        missing.append("GOOGLE_SHEETS_WEBHOOK_URL")
                    raise RuntimeError("No Google Sheets destination configured: " + ", ".join(missing))
                response = push_to_webhook(send_daily, send_summary, args.mode)
            print(json.dumps(response, indent=2))
            return 0

        if args.json:
            if args.mode == "daily":
                print(json.dumps({"daily_rows": payload["daily_rows"]}, indent=2))
            elif args.mode == "summary":
                print(json.dumps({"summary_rows": payload["summary_rows"]}, indent=2))
            else:
                print(json.dumps(payload, indent=2))
            return 0

        if args.mode in ("all", "daily"):
            print("[Daily Rows]")
            print(
                _rows_to_tsv(
                    payload["daily_rows"],
                    ["date_broker", "log_time_local", "pnl", "trades", "wins", "losses", "win_pct", "symbol", "magic"],
                )
            )
        if args.mode in ("all", "summary"):
            if args.mode == "all":
                print()
            print("[Summary Rows]")
            print(_rows_to_tsv(payload["summary_rows"], ["level", "period", "pnl", "trades", "wins", "losses", "win_pct"]))
        return 0
    except (HTTPError, URLError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
