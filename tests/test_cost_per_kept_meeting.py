"""Tests for cost_per_kept_meeting. Standard library only; temp files only."""

import contextlib
import csv
import io
import json
import random
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.dont_write_bytecode = True  # keep the source tree free of __pycache__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cost_per_kept_meeting as cpkm  # noqa: E402

SCRIPT = ROOT / "cost_per_kept_meeting.py"
EXAMPLE = ROOT / "examples" / "sources.csv"

HEADER = (
    "source,period,cost_media,cost_data,cost_tools,cost_human_time,cost_other,"
    "leads,meetings_booked,meetings_kept,meetings_qualified_kept,deals_closed,gross_margin"
)
# Reduced headers: a column that is absent from the file is not checked, whereas
# an empty cell in a present column means zero (and takes part in the checks).
SHORT = "source,period,cost_media,meetings_qualified_kept"
MID = "source,cost_media,leads,meetings_qualified_kept,gross_margin"
FUNNEL = "source,cost_media,meetings_booked,meetings_kept,meetings_qualified_kept"


class TempDirCase(unittest.TestCase):
    """Base class giving each test a private temporary directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write_csv(self, *lines, header=HEADER, name="input.csv"):
        path = self.tmp / name
        path.write_text("\n".join((header,) + lines) + "\n", encoding="utf-8")
        return path

    def run_main(self, *argv):
        """Run main() in-process; return (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cpkm.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def analyse(self, *lines, header=HEADER, min_meetings=5):
        path = self.write_csv(*lines, header=header)
        return cpkm.analyse(cpkm.load_table(str(path)), min_meetings)


class FormulaTests(TempDirCase):
    def test_hand_computed_example(self):
        # cost: 1000 + 500 + 200 + 300 + 0 = 2000
        # leads 100 -> 20.00 per lead; booked 10, kept 8 -> 80 % show rate
        # 2000 / 8 kept = 250.00; 2000 / 5 qualified kept = 400.00
        # 2000 / 2 deals = 1000.00; margin 5000 - 2000 = 3000.00
        (result,) = self.analyse("Alpha,2026-10,1000,500,200,300,,100,10,8,5,2,5000")
        self.assertEqual(result.total_cost, Decimal("2000"))
        self.assertEqual(cpkm.format_amount(result.cost_per_lead), "20.00")
        self.assertEqual(cpkm.format_amount(result.show_rate_percent), "80.00")
        self.assertEqual(cpkm.format_amount(result.cost_per_kept_meeting), "250.00")
        self.assertEqual(cpkm.format_amount(result.cost_per_qualified_kept_meeting), "400.00")
        self.assertEqual(cpkm.format_amount(result.cost_per_deal), "1000.00")
        self.assertEqual(cpkm.format_amount(result.margin_minus_cost), "3000.00")
        self.assertEqual(result.rank, 1)
        self.assertEqual(result.status, cpkm.STATUS_OK)

    def test_money_uses_decimal_and_rounds_half_up_only_for_display(self):
        # 0.10 + 0.20 is exactly 0.30 (a float sum would not be).
        (result,) = self.analyse("A,0.10,0.20,1", header="source,cost_media,cost_data,meetings_qualified_kept")
        self.assertEqual(result.total_cost, Decimal("0.30"))
        self.assertIsInstance(result.total_cost, Decimal)
        # 1.005 over 1 meeting displays as 1.01 (half up); float formatting gives 1.00.
        (result,) = self.analyse("B,,1.005,1", header=SHORT)
        self.assertEqual(result.cost_per_qualified_kept_meeting, Decimal("1.005"))
        self.assertEqual(cpkm.format_amount(result.cost_per_qualified_kept_meeting), "1.01")

    def test_missing_optional_columns_give_n_a_not_errors(self):
        header = "source,cost_media,meetings_qualified_kept"
        (result,) = self.analyse("A,300,3", header=header)
        self.assertEqual(cpkm.format_amount(result.cost_per_qualified_kept_meeting), "100.00")
        self.assertIsNone(result.cost_per_lead)
        self.assertIsNone(result.show_rate_percent)
        self.assertIsNone(result.cost_per_kept_meeting)
        self.assertIsNone(result.cost_per_deal)
        self.assertIsNone(result.margin_minus_cost)

    def test_margin_absent_means_no_margin_line_but_cost_per_deal_works(self):
        (result,) = self.analyse("A,100,2,1,", header="source,cost_media,meetings_qualified_kept,deals_closed,gross_margin")
        self.assertIsNone(result.gross_margin)
        self.assertIsNone(result.margin_minus_cost)
        self.assertEqual(cpkm.format_amount(result.cost_per_deal), "100.00")

    def test_no_deal_means_no_cost_per_deal_but_margin_minus_cost_works(self):
        (result,) = self.analyse("A,100,2,0,30", header="source,cost_media,meetings_qualified_kept,deals_closed,gross_margin")
        self.assertIsNone(result.cost_per_deal)
        self.assertEqual(cpkm.format_amount(result.margin_minus_cost), "-70.00")

    def test_negative_margin_minus_cost_is_displayed_with_sign_and_no_negative_zero(self):
        self.assertEqual(cpkm.format_amount(Decimal("-2000")), "-2000.00")
        self.assertEqual(cpkm.format_amount(Decimal("-0.001")), "0.00")


class NoShowTests(TempDirCase):
    def test_no_shows_do_not_count_as_kept(self):
        # 10 booked, only 4 took place, 2 of those were qualified. Cost 400.
        (result,) = self.analyse("A,,400,,,,,,10,4,2,,")
        self.assertEqual(cpkm.format_amount(result.cost_per_kept_meeting), "100.00")  # not 40
        self.assertEqual(cpkm.format_amount(result.show_rate_percent), "40.00")  # not 100
        self.assertEqual(cpkm.format_amount(result.cost_per_qualified_kept_meeting), "200.00")

    def test_booked_but_missed_meetings_never_change_the_ranking_metric(self):
        a, b = self.analyse(
            "Few no-shows,,400,,,,,,4,4,2,,",
            "Many no-shows,,400,,,,,,40,4,2,,",
        )
        self.assertEqual(a.cost_per_qualified_kept_meeting, b.cost_per_qualified_kept_meeting)
        self.assertEqual(
            {a.source: a.show_rate_percent, b.source: b.show_rate_percent},
            {"Few no-shows": Decimal("100"), "Many no-shows": Decimal("10")},
        )


class ZeroQualifiedTests(TempDirCase):
    LINES = (
        "Burned budget,2026-10,900,,,,,500,20,6,0,0,",
        "Good,2026-10,400,,,,,10,5,5,5,1,1000",
        "Also zero,2026-10,0,,,,,,,,0,,",
    )

    def test_no_division_error_and_ranked_last(self):
        results = self.analyse(*self.LINES)
        self.assertEqual([r.source for r in results], ["Good", "Also zero", "Burned budget"])
        for result in results[1:]:
            self.assertIsNone(result.rank)
            self.assertIsNone(result.cost_per_qualified_kept_meeting)
            self.assertEqual(result.status, cpkm.STATUS_NO_QUALIFIED)
        self.assertEqual(results[0].rank, 1)

    def test_text_json_and_csv_show_it_without_infinity_or_nan(self):
        path = self.write_csv(*self.LINES)
        code, text, _ = self.run_main(path)
        self.assertEqual(code, 0)
        self.assertIn("no qualified kept meeting", text)
        self.assertNotIn("inf", text.lower())
        self.assertNotIn("nan", text.lower())
        code, out, _ = self.run_main(path, "--json")
        document = json.loads(out)
        burned = document["results"][-1]
        self.assertEqual(burned["source"], "Burned budget")
        self.assertIsNone(burned["rank"])
        self.assertIsNone(burned["cost_per_qualified_kept_meeting"])
        self.assertEqual(burned["status"], "no_qualified_kept_meeting")
        code, out, _ = self.run_main(path, "--csv")
        rows = list(csv.DictReader(io.StringIO(out)))
        self.assertEqual(rows[-1]["cost_per_qualified_kept_meeting"], "")
        self.assertEqual(rows[-1]["rank"], "")

    def test_zero_cost_source_with_meetings_ranks_first_at_zero(self):
        results = self.analyse("Organic,,0,3", "Paid,,300,3", header=SHORT)
        self.assertEqual([r.source for r in results], ["Organic", "Paid"])
        self.assertEqual(cpkm.format_amount(results[0].cost_per_qualified_kept_meeting), "0.00")


class SmallSampleTests(TempDirCase):
    LINES = ("Tiny,,100,3", "Large,,1000,10")

    def test_flagged_but_not_hidden(self):
        results = self.analyse(*self.LINES, header=SHORT)
        by_source = {r.source: r for r in results}
        self.assertEqual(by_source["Tiny"].status, cpkm.STATUS_SMALL_SAMPLE)
        self.assertEqual(by_source["Large"].status, cpkm.STATUS_OK)
        self.assertEqual(len(results), 2)
        # Tiny is still ranked (33.33 per meeting beats 100.00).
        self.assertEqual(by_source["Tiny"].rank, 1)

    def test_threshold_is_configurable_and_exclusive(self):
        results = self.analyse(*self.LINES, header=SHORT, min_meetings=3)  # 3 is not fewer than 3
        self.assertTrue(all(r.status == cpkm.STATUS_OK for r in results))
        results = self.analyse(*self.LINES, header=SHORT, min_meetings=11)
        self.assertTrue(all(r.status == cpkm.STATUS_SMALL_SAMPLE for r in results))
        results = self.analyse(*self.LINES, header=SHORT, min_meetings=0)
        self.assertTrue(all(r.status == cpkm.STATUS_OK for r in results))

    def test_text_output_flags_small_sample_with_the_threshold(self):
        path = self.write_csv(*self.LINES, header=SHORT)
        _, text, _ = self.run_main(path, "--min-meetings", 4)
        self.assertIn("small sample", text)
        self.assertIn("fewer than 4 qualified kept meetings", text)
        self.assertIn("Tiny", text)  # not hidden

    def test_default_threshold_is_five(self):
        self.assertEqual(cpkm.DEFAULT_MIN_MEETINGS, 5)
        results = self.analyse("Four,,100,4", "Five,,100,5", header=SHORT)
        self.assertEqual(
            {r.source: r.status for r in results},
            {"Four": cpkm.STATUS_SMALL_SAMPLE, "Five": cpkm.STATUS_OK},
        )

    def test_negative_or_non_numeric_threshold_exits_2(self):
        path = self.write_csv(*self.LINES, header=SHORT)
        for bad in ("-1", "abc", "2.5"):
            with self.subTest(value=bad), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    cpkm.main([str(path), "--min-meetings", bad])
                self.assertEqual(caught.exception.code, 2)


class AggregationTests(TempDirCase):
    def test_rows_for_same_source_and_period_are_summed(self):
        (result,) = self.analyse(
            "Search,2026-10,1000,,100,,,50,10,8,4,1,3000",
            "Search,2026-10,500,,,200,,30,6,5,2,1,2000",
            "  Search  ,2026-10,,,,,100,20,4,3,1,0,",
        )
        self.assertEqual(result.input_rows, 3)
        self.assertEqual(result.total_cost, Decimal("1900"))
        self.assertEqual(result.leads, 100)
        self.assertEqual(result.meetings_booked, 20)
        self.assertEqual(result.meetings_kept, 16)
        self.assertEqual(result.meetings_qualified_kept, 7)
        self.assertEqual(result.deals_closed, 2)
        self.assertEqual(result.gross_margin, Decimal("5000"))
        # Computed on the summed totals, not by averaging row ratios.
        self.assertEqual(cpkm.format_amount(result.cost_per_qualified_kept_meeting), "271.43")
        self.assertEqual(cpkm.format_amount(result.show_rate_percent), "80.00")

    def test_different_periods_stay_separate_and_are_ranked_together(self):
        results = self.analyse("S,2026-09,500,5", "S,2026-10,200,5", header=SHORT)
        self.assertEqual([(r.period, r.rank) for r in results], [("2026-10", 1), ("2026-09", 2)])

    def test_empty_period_column_merges_periods(self):
        results = self.analyse("S,,500,5", "S,,200,5", header=SHORT)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].total_cost, Decimal("700"))

    def test_ordering_is_deterministic_whatever_the_input_order(self):
        lines = [
            "Alpha,2026-10,300,3",
            "Beta,2026-10,300,3",  # same cost as Alpha
            "Gamma,2026-10,100,5",
            "Delta,2026-10,300,6",  # same total, more meetings -> cheaper
            "Zero B,2026-10,50,0",
            "Zero A,2026-10,50,0",
        ]
        expected = ["Gamma", "Delta", "Alpha", "Beta", "Zero A", "Zero B"]
        generator = random.Random(7)
        for _ in range(10):
            generator.shuffle(lines)
            self.assertEqual([r.source for r in self.analyse(*lines, header=SHORT)], expected)

    def test_equal_costs_share_a_rank(self):
        results = self.analyse("A,,300,3", "B,,300,3", "C,,900,3", header=SHORT)
        self.assertEqual([(r.source, r.rank) for r in results], [("A", 1), ("B", 1), ("C", 3)])

    def test_mixed_margin_rows_sum_only_the_given_values(self):
        (result,) = self.analyse(
            "A,100,2,1,40",
            "A,100,2,0,",
            header="source,cost_media,meetings_qualified_kept,deals_closed,gross_margin",
        )
        self.assertEqual(result.gross_margin, Decimal("40"))
        self.assertEqual(result.margin_minus_cost, Decimal("-160"))


class ValidationTests(TempDirCase):
    def assert_input_error(self, lines, *, header=HEADER, expect, row=None):
        path = self.write_csv(*lines, header=header)
        code, out, err = self.run_main(path)
        self.assertEqual(code, 2, msg=err)
        self.assertEqual(out, "")
        self.assertIn(expect, err)
        if row is not None:
            self.assertIn("row %d:" % row, err)
        return err

    def test_negative_number(self):
        self.assert_input_error(
            ["A,100,10,1,", "B,-5,10,1,"], header=MID, expect="must not be negative", row=3
        )
        self.assert_input_error(
            ["A,100,10,-1,"], header=MID, expect="must not be negative", row=2
        )
        self.assert_input_error(["A,100,10,1,-3"], header=MID, expect="gross_margin", row=2)

    def test_non_numeric(self):
        for bad in ("abc", "12,5", "1_000", "NaN", "Infinity", "$10", "1e999"):
            with self.subTest(value=bad):
                self.assert_input_error(
                    ["A,100,10,1,", "A,100,10,1,", 'B,"%s",10,1,' % bad],
                    header=MID,
                    expect="cost_media",
                    row=4,
                )

    def test_fractional_count(self):
        self.assert_input_error(["A,100,2.5,1,"], header=MID, expect="whole number", row=2)

    def test_kept_greater_than_booked(self):
        err = self.assert_input_error(
            ["A,,100,,,,,,5,6,1,,"], expect="meetings_kept (6) exceeds meetings_booked (5)", row=2
        )
        self.assertTrue(err.startswith("error: "))

    def test_qualified_kept_greater_than_kept(self):
        self.assert_input_error(
            ["A,,100,,,,,,9,5,6,,"],
            expect="meetings_qualified_kept (6) exceeds meetings_kept (5)",
            row=2,
        )

    def test_deals_greater_than_qualified_kept(self):
        self.assert_input_error(
            ["A,,100,,,,,,9,5,3,4,"],
            expect="deals_closed (4) exceeds meetings_qualified_kept (3)",
            row=2,
        )

    def test_funnel_check_skips_absent_columns_but_still_compares_present_ones(self):
        header = "source,cost_media,meetings_booked,meetings_qualified_kept"
        self.assert_input_error(
            ["A,100,2,3"],
            header=header,
            expect="meetings_qualified_kept (3) exceeds meetings_booked (2)",
            row=2,
        )
        self.assertEqual(len(self.analyse("A,100,3,3", header=header)), 1)

    def test_every_bad_row_is_reported(self):
        err = self.assert_input_error(
            ["A,-1,2,1,1", "B,100,2,2,1", "C,100,1,2,1"], header=FUNNEL, expect="row 2:"
        )
        self.assertIn("row 4:", err)
        self.assertNotIn("row 3:", err)

    def test_empty_source(self):
        self.assert_input_error([",,100,1"], header=SHORT, expect="'source' must not be empty", row=2)

    def test_header_problems(self):
        self.assert_input_error(
            ["A,100"], header="source,cost_media", expect="missing required column(s): meetings_qualified_kept"
        )
        self.assert_input_error(
            ["100,1"], header="cost_media,meetings_qualified_kept", expect="source"
        )
        self.assert_input_error(
            ["A,1"], header="source,meetings_qualified_kept", expect="at least one cost column"
        )
        self.assert_input_error(
            ["A,1,1,1"],
            header="source,cost_media,cost_media,meetings_qualified_kept",
            expect="duplicate column 'cost_media'",
        )

    def test_ragged_row(self):
        self.assert_input_error(["A,2026-10,100"], expect="expected 13 cells", row=2)

    def test_empty_and_header_only_files(self):
        path = self.tmp / "empty.csv"
        path.write_text("", encoding="utf-8")
        code, _, err = self.run_main(path)
        self.assertEqual(code, 2)
        self.assertIn("empty", err)
        code, _, err = self.run_main(self.write_csv())
        self.assertEqual(code, 2)
        self.assertIn("no data rows", err)

    def test_missing_file_and_bad_encoding(self):
        code, _, err = self.run_main(self.tmp / "absent.csv")
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)
        path = self.tmp / "latin1.csv"
        path.write_bytes(SHORT.encode() + b"\nCaf\xe9,,1,1\n")
        code, _, err = self.run_main(path)
        self.assertEqual(code, 2)
        self.assertIn("UTF-8", err)

    def test_row_number_counts_blank_lines_and_the_header(self):
        path = self.tmp / "gaps.csv"
        path.write_text(SHORT + "\nA,,1,1\n\n\nB,,x,1\n", encoding="utf-8")
        code, _, err = self.run_main(path)
        self.assertEqual(code, 2)
        self.assertIn("row 5:", err)

    def test_header_is_case_insensitive_bom_tolerant_and_unknown_columns_warn(self):
        path = self.tmp / "bom.csv"
        path.write_bytes(
            b"\xef\xbb\xbf Source ,COST_MEDIA,Meetings_Qualified_Kept,notes\nA,100,2,hello\n"
        )
        code, out, err = self.run_main(path)
        self.assertEqual(code, 0)
        self.assertIn("warning: ignoring unknown column(s): notes", err)
        self.assertIn("50.00", out)


class OutputTests(TempDirCase):
    LINES = (
        "Beta,2026-10,300,,,,,60,10,8,3,1,900",
        "Alpha,2026-10,,200,,,,40,5,5,5,,",
        "Idle,2026-10,50,,,,,10,2,1,0,,",
    )

    def test_json_exact_and_stable(self):
        path = self.write_csv(*self.LINES)
        _, first, _ = self.run_main(path, "--json", "--min-meetings", 4)
        _, second, _ = self.run_main(path, "--json", "--min-meetings", 4)
        self.assertEqual(first, second)
        document = json.loads(first)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["min_meetings"], 4)
        self.assertEqual([r["source"] for r in document["results"]], ["Alpha", "Beta", "Idle"])
        self.assertEqual(
            document["results"][0],
            {
                "rank": 1,
                "source": "Alpha",
                "period": "2026-10",
                "status": "ok",
                "input_rows": 1,
                "total_cost": "200.00",
                "cost_media": "0.00",
                "cost_data": "200.00",
                "cost_tools": "0.00",
                "cost_human_time": "0.00",
                "cost_other": "0.00",
                "leads": 40,
                "meetings_booked": 5,
                "meetings_kept": 5,
                "meetings_qualified_kept": 5,
                "deals_closed": 0,
                "gross_margin": None,
                "cost_per_lead": "5.00",
                "show_rate_percent": "100.00",
                "cost_per_kept_meeting": "40.00",
                "cost_per_qualified_kept_meeting": "40.00",
                "cost_per_deal": None,
                "margin_minus_cost": None,
            },
        )
        self.assertEqual(document["results"][1]["status"], "small_sample")
        self.assertEqual(document["results"][1]["margin_minus_cost"], "600.00")
        self.assertEqual(document["results"][2]["status"], "no_qualified_kept_meeting")
        self.assertTrue(first.endswith("}\n"))

    def test_csv_exact_and_stable(self):
        path = self.write_csv(*self.LINES)
        _, first, _ = self.run_main(path, "--csv")
        _, second, _ = self.run_main(path, "--csv")
        self.assertEqual(first, second)
        lines = first.splitlines()
        self.assertEqual(
            lines[0],
            "rank,source,period,status,input_rows,total_cost,cost_media,cost_data,cost_tools,"
            "cost_human_time,cost_other,leads,meetings_booked,meetings_kept,"
            "meetings_qualified_kept,deals_closed,gross_margin,cost_per_lead,show_rate_percent,"
            "cost_per_kept_meeting,cost_per_qualified_kept_meeting,cost_per_deal,"
            "margin_minus_cost",
        )
        self.assertEqual(
            lines[1],
            "1,Alpha,2026-10,ok,1,200.00,0.00,200.00,0.00,0.00,0.00,40,5,5,5,0,,5.00,100.00,"
            "40.00,40.00,,",
        )
        self.assertEqual(
            lines[3],
            ",Idle,2026-10,no_qualified_kept_meeting,1,50.00,50.00,0.00,0.00,0.00,0.00,10,2,1,0,"
            "0,,5.00,50.00,50.00,,,",
        )
        self.assertEqual(len(lines), 4)

    def test_json_and_csv_keys_do_not_depend_on_language(self):
        path = self.write_csv(*self.LINES)
        self.assertEqual(self.run_main(path, "--json")[1], self.run_main(path, "--json", "--lang", "fr")[1])
        self.assertEqual(self.run_main(path, "--csv")[1], self.run_main(path, "--csv", "--lang", "fr")[1])

    def test_text_table_is_aligned_markdown_with_a_fixed_footer(self):
        path = self.write_csv(*self.LINES)
        _, text, _ = self.run_main(path)
        table = [line for line in text.splitlines() if line.startswith("|")]
        self.assertEqual(len(table), 5)  # header, separator, three rows
        self.assertEqual(len({len(line) for line in table}), 1)  # aligned
        self.assertIn("Cost per qualified kept meeting", table[0])
        self.assertTrue(text.rstrip().endswith("check closed deals and margin minus cost."))
        self.assertIn("no qualified kept meeting", text)
        self.assertEqual(text, self.run_main(path)[1])

    def test_french_labels(self):
        path = self.write_csv(*self.LINES)
        _, text, _ = self.run_main(path, "--lang", "fr", "--min-meetings", 4)
        self.assertIn("Coût par RDV qualifié tenu", text)
        self.assertIn("Taux de présence", text)
        self.assertIn("aucun RDV qualifié tenu", text)
        self.assertIn("petit échantillon", text)
        self.assertIn("ne prouve pas la rentabilité", text)
        self.assertNotIn("Cost per", text)

    def test_pipe_in_source_name_does_not_break_the_table(self):
        path = self.write_csv('"A|B",,100,1', header=SHORT)
        _, text, _ = self.run_main(path)
        self.assertIn("A\\|B", text)

    def test_json_and_csv_are_mutually_exclusive(self):
        path = self.write_csv(*self.LINES)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cpkm.main([str(path), "--json", "--csv"])
        self.assertEqual(caught.exception.code, 2)

    def test_example_file_shows_cheapest_lead_is_not_cheapest_meeting(self):
        results = cpkm.analyse(cpkm.load_table(str(EXAMPLE)))
        by_cost_per_lead = sorted(results, key=lambda r: r.cost_per_lead)
        by_cost_per_meeting = [r.source for r in results]
        self.assertEqual(by_cost_per_lead[0].source, "Example Purchased Lists")
        self.assertEqual(by_cost_per_meeting[-1], "Example Purchased Lists")
        self.assertEqual(by_cost_per_meeting[0], "Example Partner Referrals")
        self.assertLess(results[-1].margin_minus_cost, 0)


class CommandLineTests(TempDirCase):
    def run_cli(self, *args, stdin=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + [str(a) for a in args],
            cwd=str(self.tmp),
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
            timeout=60,
        )

    def test_end_to_end_text(self):
        done = self.run_cli(EXAMPLE)
        self.assertEqual(done.returncode, 0, msg=done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertIn("Example Partner Referrals", done.stdout)
        lines = [line for line in done.stdout.splitlines() if line.startswith("| ")]
        self.assertEqual(len(lines), 6)
        self.assertLess(done.stdout.index("Partner Referrals"), done.stdout.index("Purchased Lists"))

    def test_end_to_end_json_and_csv_and_french(self):
        done = self.run_cli(EXAMPLE, "--json")
        self.assertEqual(done.returncode, 0, msg=done.stderr)
        self.assertEqual(
            [r["rank"] for r in json.loads(done.stdout)["results"]], [1, 2, 3, 4]
        )
        done = self.run_cli(EXAMPLE, "--csv")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(done.stdout)))), 4)
        done = self.run_cli(EXAMPLE, "--lang", "fr", "--min-meetings", 10)
        self.assertEqual(done.returncode, 0)
        self.assertIn("petit échantillon", done.stdout)

    def test_end_to_end_invalid_input_exits_2_with_the_row(self):
        path = self.tmp / "bad.csv"
        path.write_text(FUNNEL + "\nA,1,2,2,1\nB,1,2,3,1\n", encoding="utf-8")
        done = self.run_cli(path)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertIn("row 3: meetings_kept (3) exceeds meetings_booked (2)", done.stderr)

    def test_end_to_end_bad_option_and_missing_file_exit_2(self):
        self.assertEqual(self.run_cli(EXAMPLE, "--lang", "de").returncode, 2)
        done = self.run_cli(self.tmp / "nope.csv")
        self.assertEqual(done.returncode, 2)
        self.assertIn("cannot read", done.stderr)

    def test_reads_standard_input(self):
        done = self.run_cli("-", "--json", stdin=SHORT + "\nA,,100,2\n")
        self.assertEqual(done.returncode, 0, msg=done.stderr)
        self.assertEqual(json.loads(done.stdout)["results"][0]["cost_per_qualified_kept_meeting"], "50.00")

    def test_cli_writes_nothing_into_its_working_directory(self):
        before = sorted(p.name for p in self.tmp.iterdir())
        self.run_cli(EXAMPLE)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
