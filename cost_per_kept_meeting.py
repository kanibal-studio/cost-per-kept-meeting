#!/usr/bin/env python3
"""Rank B2B lead sources on cost per KEPT qualified meeting, not cost per lead.

Method
------
Over one period, divide ALL acquisition costs of a source (media, data, tools,
human time, other fees) by the number of qualified meetings that actually took
place. A meeting that was booked and then cancelled, or missed (no-show), is
not a kept meeting and never enters a denominator.

    total cost                   = media + data + tools + human time + other
    cost per lead                = total cost / leads
    show rate                    = meetings kept / meetings booked
    cost per kept meeting        = total cost / meetings kept
    cost per qualified kept mtg  = total cost / qualified kept meetings
    cost per deal                = total cost / deals closed
    margin minus cost            = gross margin - total cost

A low cost per meeting does not prove profitability, so when deals and margin
are supplied the tool also reports cost per deal and margin minus cost.

Every amount is a ``decimal.Decimal``; rounding (2 decimals, half up) happens
only when a value is displayed. A division by zero never raises: the metric is
reported as not available (``n/a``), and a source without any qualified kept
meeting is listed last with the status ``no_qualified_kept_meeting``.

Command line::

    python3 cost_per_kept_meeting.py sources.csv [--json | --csv]
                                     [--min-meetings N] [--lang en|fr]

Exit codes: 0 success, 2 invalid input (a message names the row at fault).

Python 3.9+, standard library only. MIT licensed.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, FrozenSet, List, Optional, Sequence, TextIO, Tuple

__version__ = "0.1.1"

EXIT_OK = 0
EXIT_INVALID_INPUT = 2

DEFAULT_MIN_MEETINGS = 5
MAX_ERRORS_SHOWN = 50

COST_COLUMNS = ("cost_media", "cost_data", "cost_tools", "cost_human_time", "cost_other")
LEADS_COLUMN = "leads"
# Funnel columns, widest stage first. Each stage may not exceed the previous
# stage that is present in the file.
FUNNEL_COLUMNS = ("meetings_booked", "meetings_kept", "meetings_qualified_kept", "deals_closed")
COUNT_COLUMNS = (LEADS_COLUMN,) + FUNNEL_COLUMNS
MARGIN_COLUMN = "gross_margin"
KNOWN_COLUMNS = ("source", "period") + COST_COLUMNS + COUNT_COLUMNS + (MARGIN_COLUMN,)
REQUIRED_COLUMNS = ("source", "meetings_qualified_kept")

STATUS_OK = "ok"
STATUS_SMALL_SAMPLE = "small_sample"
STATUS_NO_QUALIFIED = "no_qualified_kept_meeting"

_NUMBER_RE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_MAX_ADJUSTED_EXPONENT = 15  # values at or above 10**16 are rejected as implausible
_CENTS = Decimal("0.01")
_ZERO = Decimal(0)
_HUNDRED = Decimal(100)


class InputError(Exception):
    """The input file is invalid. ``messages`` lists every problem found."""

    def __init__(self, messages: Sequence[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = list(messages)


# --------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------


@dataclass
class Record:
    """One validated CSV data row."""

    line: int
    source: str
    period: str
    costs: Dict[str, Decimal]
    counts: Dict[str, int]
    margin: Optional[Decimal]


@dataclass
class Table:
    """Validated input: records plus the set of known columns the file has."""

    records: List[Record]
    columns: FrozenSet[str]
    warnings: List[str] = field(default_factory=list)


def _parse_amount(text: str, column: str) -> Decimal:
    """Parse a non-negative decimal number; empty text means zero."""
    if text == "":
        return _ZERO
    if not _NUMBER_RE.match(text):
        raise ValueError(
            "column '%s' must be a number with a dot as decimal separator, got %r" % (column, text)
        )
    value = Decimal(text)
    if value < 0:
        raise ValueError("column '%s' must not be negative, got %s" % (column, text))
    if value != 0 and value.adjusted() > _MAX_ADJUSTED_EXPONENT:
        raise ValueError("column '%s' is implausibly large, got %s" % (column, text))
    return value


def _parse_count(text: str, column: str) -> int:
    """Parse a non-negative whole number; empty text means zero."""
    value = _parse_amount(text, column)
    if value != value.to_integral_value():
        raise ValueError("column '%s' must be a whole number, got %s" % (column, text))
    return int(value)


def _parse_record(
    row_no: int, cells: Sequence[str], index: Dict[str, int]
) -> Tuple[Optional[Record], List[str]]:
    """Validate one data row. Returns the record (or None) and its errors."""
    errors: List[str] = []

    def cell(column: str) -> str:
        return cells[index[column]].strip()

    source = cell("source")
    if not source:
        errors.append("row %d: column 'source' must not be empty" % row_no)
    period = cell("period") if "period" in index else ""

    costs: Dict[str, Decimal] = {}
    for column in COST_COLUMNS:
        if column in index:
            try:
                costs[column] = _parse_amount(cell(column), column)
            except ValueError as exc:
                errors.append("row %d: %s" % (row_no, exc))

    counts: Dict[str, int] = {}
    for column in COUNT_COLUMNS:
        if column in index:
            try:
                counts[column] = _parse_count(cell(column), column)
            except ValueError as exc:
                errors.append("row %d: %s" % (row_no, exc))

    margin: Optional[Decimal] = None
    if MARGIN_COLUMN in index and cell(MARGIN_COLUMN) != "":
        try:
            margin = _parse_amount(cell(MARGIN_COLUMN), MARGIN_COLUMN)
        except ValueError as exc:
            errors.append("row %d: %s" % (row_no, exc))

    # Funnel consistency: kept <= booked, qualified kept <= kept, deals <= qualified kept.
    previous: Optional[str] = None
    for column in FUNNEL_COLUMNS:
        if column not in counts:
            continue
        if previous is not None and counts[column] > counts[previous]:
            errors.append(
                "row %d: %s (%d) exceeds %s (%d)"
                % (row_no, column, counts[column], previous, counts[previous])
            )
        previous = column

    if errors:
        return None, errors
    return Record(row_no, source, period, costs, counts, margin), []


def parse_csv(handle: TextIO) -> Table:
    """Read and strictly validate a CSV file; raise InputError listing problems.

    Row numbers in messages are file line numbers: the header is row 1 and the
    first data row is row 2 (a record spanning several lines is reported at its
    first line).
    """
    reader = csv.reader(handle)
    try:
        header = next(reader, None)
    except csv.Error as exc:
        raise InputError(["row 1: unreadable CSV (%s)" % exc])
    if header is None or not any(cell.strip() for cell in header):
        raise InputError(["row 1: the file is empty; a header row with column names is required"])

    names = [cell.lstrip("\ufeff").strip().lower() for cell in header]
    problems: List[str] = []
    seen = set()
    for name in names:
        if name and name in seen:
            problems.append("row 1: duplicate column '%s'" % name)
        seen.add(name)
    missing = [column for column in REQUIRED_COLUMNS if column not in seen]
    if missing:
        problems.append("row 1: missing required column(s): %s" % ", ".join(missing))
    if not any(column in seen for column in COST_COLUMNS):
        problems.append(
            "row 1: at least one cost column is required (%s)" % ", ".join(COST_COLUMNS)
        )
    if problems:
        raise InputError(problems)

    index = {name: position for position, name in enumerate(names) if name in KNOWN_COLUMNS}
    unknown = [name for name in names if name and name not in KNOWN_COLUMNS]
    warnings = []
    if unknown:
        warnings.append("ignoring unknown column(s): %s" % ", ".join(unknown))

    records: List[Record] = []
    errors: List[str] = []
    previous_line = reader.line_num
    try:
        for cells in reader:
            row_no = previous_line + 1
            previous_line = reader.line_num
            if not any(cell.strip() for cell in cells):
                continue  # blank line
            if len(cells) != len(names):
                errors.append(
                    "row %d: expected %d cells as in the header, found %d"
                    % (row_no, len(names), len(cells))
                )
                continue
            record, row_errors = _parse_record(row_no, cells, index)
            errors.extend(row_errors)
            if record is not None:
                records.append(record)
    except csv.Error as exc:
        errors.append("row %d: unreadable CSV (%s)" % (reader.line_num, exc))

    if errors:
        raise InputError(errors)
    if not records:
        raise InputError(["no data rows found below the header"])
    return Table(records, frozenset(index), warnings)


def load_table(path: str) -> Table:
    """Open ``path`` ('-' means standard input) as UTF-8 and parse it."""
    try:
        if path == "-":
            return parse_csv(io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig", newline=""))
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return parse_csv(handle)
    except UnicodeDecodeError:
        raise InputError(["%s is not valid UTF-8 text" % path])
    except OSError as exc:
        raise InputError(["cannot read %s: %s" % (path, exc.strerror or exc)])


# --------------------------------------------------------------------------
# Computation
# --------------------------------------------------------------------------


@dataclass
class SourceResult:
    """Metrics for one source and period (all rows of the group summed)."""

    source: str
    period: str
    input_rows: int
    costs: Dict[str, Decimal]
    total_cost: Decimal
    leads: Optional[int]
    meetings_booked: Optional[int]
    meetings_kept: Optional[int]
    meetings_qualified_kept: int
    deals_closed: Optional[int]
    gross_margin: Optional[Decimal]
    cost_per_lead: Optional[Decimal]
    show_rate_percent: Optional[Decimal]
    cost_per_kept_meeting: Optional[Decimal]
    cost_per_qualified_kept_meeting: Optional[Decimal]
    cost_per_deal: Optional[Decimal]
    margin_minus_cost: Optional[Decimal]
    status: str
    rank: Optional[int] = None


def _ratio(numerator: Decimal, denominator: Optional[int]) -> Optional[Decimal]:
    """numerator / denominator, or None when the denominator is missing or zero."""
    if not denominator:
        return None
    return numerator / Decimal(denominator)


def _sum_count(records: Sequence[Record], column: str, columns: FrozenSet[str]) -> Optional[int]:
    """Sum a count column, or None when the file has no such column."""
    if column not in columns:
        return None
    return sum(record.counts[column] for record in records)


def _summarise(
    key: Tuple[str, str], records: Sequence[Record], columns: FrozenSet[str], min_meetings: int
) -> SourceResult:
    costs = {
        column: sum((r.costs.get(column, _ZERO) for r in records), _ZERO) for column in COST_COLUMNS
    }
    total_cost = sum(costs.values(), _ZERO)
    leads = _sum_count(records, LEADS_COLUMN, columns)
    booked = _sum_count(records, "meetings_booked", columns)
    kept = _sum_count(records, "meetings_kept", columns)
    qualified = _sum_count(records, "meetings_qualified_kept", columns) or 0
    deals = _sum_count(records, "deals_closed", columns)
    margins = [r.margin for r in records if r.margin is not None]
    gross_margin = sum(margins, _ZERO) if margins else None

    show_rate = _ratio(Decimal(kept) * _HUNDRED, booked) if kept is not None else None
    if qualified == 0:
        status = STATUS_NO_QUALIFIED
    elif qualified < min_meetings:
        status = STATUS_SMALL_SAMPLE
    else:
        status = STATUS_OK

    return SourceResult(
        source=key[0],
        period=key[1],
        input_rows=len(records),
        costs=costs,
        total_cost=total_cost,
        leads=leads,
        meetings_booked=booked,
        meetings_kept=kept,
        meetings_qualified_kept=qualified,
        deals_closed=deals,
        gross_margin=gross_margin,
        cost_per_lead=_ratio(total_cost, leads),
        show_rate_percent=show_rate,
        cost_per_kept_meeting=_ratio(total_cost, kept),
        cost_per_qualified_kept_meeting=_ratio(total_cost, qualified),
        cost_per_deal=_ratio(total_cost, deals),
        margin_minus_cost=None if gross_margin is None else gross_margin - total_cost,
        status=status,
    )


def analyse(table: Table, min_meetings: int = DEFAULT_MIN_MEETINGS) -> List[SourceResult]:
    """Sum rows per (source, period), compute metrics, and rank the groups.

    Ranked groups come first, lowest cost per qualified kept meeting first
    (ties: more qualified meetings, then source, then period). Groups without a
    qualified kept meeting come last, ordered by source then period. Groups with
    exactly the same cost share a rank number.
    """
    groups: Dict[Tuple[str, str], List[Record]] = {}
    for record in table.records:
        groups.setdefault((record.source, record.period), []).append(record)
    results = [
        _summarise(key, records, table.columns, min_meetings) for key, records in groups.items()
    ]

    ranked = [r for r in results if r.cost_per_qualified_kept_meeting is not None]
    unranked = [r for r in results if r.cost_per_qualified_kept_meeting is None]
    ranked.sort(
        key=lambda r: (
            r.cost_per_qualified_kept_meeting,
            -r.meetings_qualified_kept,
            r.source,
            r.period,
        )
    )
    unranked.sort(key=lambda r: (r.source, r.period))

    previous_cost: Optional[Decimal] = None
    previous_rank = 0
    for position, result in enumerate(ranked, start=1):
        if result.cost_per_qualified_kept_meeting != previous_cost:
            previous_rank = position
            previous_cost = result.cost_per_qualified_kept_meeting
        result.rank = previous_rank
    return ranked + unranked


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def format_amount(value: Optional[Decimal]) -> Optional[str]:
    """Round to 2 decimals (half up) for display; None stays None."""
    if value is None:
        return None
    rounded = value.quantize(_CENTS, rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)  # never display "-0.00"
    return format(rounded, "f")


def result_to_dict(result: SourceResult) -> Dict[str, object]:
    """Stable machine-readable form of one result (used by JSON and CSV)."""
    row: Dict[str, object] = {
        "rank": result.rank,
        "source": result.source,
        "period": result.period,
        "status": result.status,
        "input_rows": result.input_rows,
        "total_cost": format_amount(result.total_cost),
    }
    for column in COST_COLUMNS:
        row[column] = format_amount(result.costs[column])
    row.update(
        {
            "leads": result.leads,
            "meetings_booked": result.meetings_booked,
            "meetings_kept": result.meetings_kept,
            "meetings_qualified_kept": result.meetings_qualified_kept,
            "deals_closed": result.deals_closed,
            "gross_margin": format_amount(result.gross_margin),
            "cost_per_lead": format_amount(result.cost_per_lead),
            "show_rate_percent": format_amount(result.show_rate_percent),
            "cost_per_kept_meeting": format_amount(result.cost_per_kept_meeting),
            "cost_per_qualified_kept_meeting": format_amount(
                result.cost_per_qualified_kept_meeting
            ),
            "cost_per_deal": format_amount(result.cost_per_deal),
            "margin_minus_cost": format_amount(result.margin_minus_cost),
        }
    )
    return row


def render_json(results: Sequence[SourceResult], min_meetings: int) -> str:
    """JSON document: amounts are strings rounded to 2 decimals, n/a is null."""
    document = {
        "schema_version": 1,
        "min_meetings": min_meetings,
        "results": [result_to_dict(result) for result in results],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def render_csv(results: Sequence[SourceResult]) -> str:
    """CSV with one row per source and period; n/a is an empty cell."""
    rows = [result_to_dict(result) for result in results]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(list(rows[0].keys()) if rows else [])
    for row in rows:
        writer.writerow(["" if value is None else value for value in row.values()])
    return buffer.getvalue()


LABELS: Dict[str, Dict[str, str]] = {
    "en": {
        "title": "Cost per kept qualified meeting, lowest first "
        "(amounts in the currency of the input file)",
        "rank": "Rank",
        "source": "Source",
        "period": "Period",
        "total_cost": "Total cost",
        "cost_per_lead": "Cost per lead",
        "show_rate": "Show rate",
        "cost_per_kept": "Cost per kept meeting",
        "cost_per_qualified": "Cost per qualified kept meeting",
        "qualified": "Qualified kept",
        "cost_per_deal": "Cost per deal",
        "margin_minus_cost": "Margin minus cost",
        "note": "Note",
        "na": "n/a",
        "small_sample": "small sample",
        "no_qualified": "no qualified kept meeting",
        "legend_small": "small sample: fewer than {n} qualified kept meetings; "
        "read the figure with caution (the row is not hidden).",
        "legend_none": "no qualified kept meeting: listed last, "
        "no cost per meeting can be computed.",
        "caution": "A low cost per meeting does not prove profitability: "
        "check closed deals and margin minus cost.",
    },
    "fr": {
        "title": "Coût par rendez-vous qualifié tenu, du plus bas au plus haut "
        "(montants dans la devise du fichier d'entrée)",
        "rank": "Rang",
        "source": "Source",
        "period": "Période",
        "total_cost": "Coût total",
        "cost_per_lead": "Coût par lead",
        "show_rate": "Taux de présence",
        "cost_per_kept": "Coût par RDV tenu",
        "cost_per_qualified": "Coût par RDV qualifié tenu",
        "qualified": "RDV qualifiés tenus",
        "cost_per_deal": "Coût par affaire",
        "margin_minus_cost": "Marge moins coût",
        "note": "Note",
        "na": "n/d",
        "small_sample": "petit échantillon",
        "no_qualified": "aucun RDV qualifié tenu",
        "legend_small": "petit échantillon : moins de {n} rendez-vous qualifiés tenus ; "
        "à lire avec prudence (la ligne n'est pas masquée).",
        "legend_none": "aucun RDV qualifié tenu : classé en dernier, "
        "aucun coût par rendez-vous n'est calculable.",
        "caution": "Un faible coût par rendez-vous ne prouve pas la rentabilité : "
        "vérifiez les affaires conclues et la marge moins le coût.",
    },
}


def _table_cell(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _markdown_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], right_aligned: Sequence[bool]
) -> List[str]:
    """Render an aligned Markdown table (readable as plain text too)."""
    widths = [
        max(3, len(header), *(len(row[i]) for row in rows)) for i, header in enumerate(headers)
    ]

    def line(cells: Sequence[str]) -> str:
        padded = [
            cell.rjust(widths[i]) if right_aligned[i] else cell.ljust(widths[i])
            for i, cell in enumerate(cells)
        ]
        return "| " + " | ".join(padded) + " |"

    separator = [
        "-" * (widths[i] - 1) + ":" if right_aligned[i] else ":" + "-" * (widths[i] - 1)
        for i in range(len(headers))
    ]
    return [line(headers), "| " + " | ".join(separator) + " |"] + [line(row) for row in rows]


def render_text(results: Sequence[SourceResult], lang: str, min_meetings: int) -> str:
    """Markdown/plain-text report. ``lang`` selects the labels (en or fr)."""
    labels = LABELS[lang]
    na = labels["na"]

    def amount(value: Optional[Decimal]) -> str:
        text = format_amount(value)
        return na if text is None else text

    headers = [
        labels[key]
        for key in (
            "rank",
            "source",
            "period",
            "total_cost",
            "cost_per_lead",
            "show_rate",
            "cost_per_kept",
            "cost_per_qualified",
            "qualified",
            "cost_per_deal",
            "margin_minus_cost",
            "note",
        )
    ]
    right_aligned = [True, False, False] + [True] * 8 + [False]
    rows = []
    for result in results:
        note = ""
        if result.status == STATUS_SMALL_SAMPLE:
            note = labels["small_sample"]
        elif result.status == STATUS_NO_QUALIFIED:
            note = labels["no_qualified"]
        show_rate = format_amount(result.show_rate_percent)
        rows.append(
            [
                "-" if result.rank is None else str(result.rank),
                _table_cell(result.source),
                _table_cell(result.period) if result.period else "-",
                amount(result.total_cost),
                amount(result.cost_per_lead),
                na if show_rate is None else show_rate + "%",
                amount(result.cost_per_kept_meeting),
                amount(result.cost_per_qualified_kept_meeting),
                str(result.meetings_qualified_kept),
                amount(result.cost_per_deal),
                amount(result.margin_minus_cost),
                note,
            ]
        )

    lines = [labels["title"], ""] + _markdown_table(headers, rows, right_aligned) + [""]
    if any(r.status == STATUS_SMALL_SAMPLE for r in results):
        lines.append(labels["legend_small"].format(n=min_meetings))
    if any(r.status == STATUS_NO_QUALIFIED for r in results):
        lines.append(labels["legend_none"])
    lines.append(labels["caution"])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number, got %r" % text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative, got %s" % text)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cost_per_kept_meeting",
        description="Rank B2B lead sources on cost per kept qualified meeting "
        "instead of cost per lead. Reads a UTF-8 CSV file; no network access.",
        epilog="Exit codes: 0 success, 2 invalid input.",
    )
    parser.add_argument("csv_file", help="path of the CSV file ('-' reads standard input)")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print JSON instead of a table")
    output.add_argument("--csv", action="store_true", help="print CSV instead of a table")
    parser.add_argument(
        "--min-meetings",
        type=_non_negative_int,
        default=DEFAULT_MIN_MEETINGS,
        metavar="N",
        help="flag results based on fewer than N qualified kept meetings as "
        "'small sample' (default: %(default)s)",
    )
    parser.add_argument(
        "--lang",
        choices=sorted(LABELS),
        default="en",
        help="labels of the text table (default: en); JSON and CSV keys never change",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns the exit code (0 ok, 2 invalid input)."""
    args = build_parser().parse_args(argv)  # argparse itself exits with 2 on bad options
    try:
        table = load_table(args.csv_file)
    except InputError as exc:
        shown = exc.messages[:MAX_ERRORS_SHOWN]
        for message in shown:
            sys.stderr.write("error: %s\n" % message)
        if len(exc.messages) > len(shown):
            sys.stderr.write("error: ... and %d more\n" % (len(exc.messages) - len(shown)))
        return EXIT_INVALID_INPUT

    for warning in table.warnings:
        sys.stderr.write("warning: %s\n" % warning)

    results = analyse(table, args.min_meetings)
    if args.json:
        output = render_json(results, args.min_meetings)
    elif args.csv:
        output = render_csv(results)
    else:
        output = render_text(results, args.lang, args.min_meetings)
    sys.stdout.write(output)
    return EXIT_OK


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
