#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-CSSL-1.0
"""Check parse_output.py against real output saved in testdata/ (ctest -V logs and benchmark JSON)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import aggregate  # noqa: E402
import parse_output  # noqa: E402

DATA = os.path.join(HERE, "testdata")


def rows_of(name):
    return parse_output.parse_file(os.path.join(DATA, name), test=name, run=1)


def find(rows, name, block=0, depth=None, section=None):
    hits = [r for r in rows if r["name"] == name and r["block"] == block
            and (depth is None or r["depth"] == depth) and (section is None or r["section"] == section)]
    assert len(hits) == 1, "{} hits for {} in block {}".format(len(hits), name, block)
    return hits[0]


class DlsimTest(unittest.TestCase):
    rows = rows_of("physim.5g.nr_dlsim.basic.test1.txt")

    def test_blocks_keyed_by_snr(self):
        snrs = sorted({(r["block"], r["snr"]) for r in self.rows})
        self.assertEqual(snrs, [(0, "5.000000"), (1, "5.200000"), (2, "5.400000")])

    def test_root_line_with_statistics(self):
        r = find(self.rows, "PHY proc tx")
        self.assertEqual((r["mean_us"], r["trials"], r["depth"]), (29.32, 599, 0))
        self.assertEqual((r["std_us"], r["min_us"], r["q1_us"]), (7.85, 25.58, 27.25))
        self.assertEqual((r["median_us"], r["q3_us"], r["max_us"], r["stat_trials"]), (29.33, 29.67, 53.04, 599))

    def test_indented_lines_ignore_total_column(self):
        r = find(self.rows, "DLSCH encoding time")
        self.assertEqual((r["mean_us"], r["trials"], r["depth"]), (19.65, 599, 2))
        self.assertNotIn("total", " ".join(r.keys()))
        r = find(self.rows, "LDPC encoding time")
        self.assertEqual((r["mean_us"], r["trials"], r["depth"]), (16.87, 599, 3))

    def test_same_name_at_two_depths_kept_apart(self):
        a = find(self.rows, "DLSCH scrambling time", depth=2)
        b = find(self.rows, "DLSCH scrambling time", depth=3)
        self.assertNotEqual(a["stage"], b["stage"])

    def test_cumulative_counter_reported_as_printed(self):
        self.assertEqual(find(self.rows, "Multipath Channel (CPU)", block=0)["trials"], 599)
        self.assertEqual(find(self.rows, "Multipath Channel (CPU)", block=1)["trials"], 1184)


class UlsimTest(unittest.TestCase):
    rows = rows_of("physim.5g.nr_ulsim.misc.test1.txt")

    def test_six_snr_blocks(self):
        self.assertEqual(len({r["block"] for r in self.rows}), 6)

    def test_sections(self):
        r = find(self.rows, "PHY_PROC_TX", section="UE TX")
        self.assertEqual((r["mean_us"], r["trials"]), (57.45, 600))
        r = find(self.rows, "Total PHY proc rx", section="gNB RX")
        self.assertEqual((r["mean_us"], r["trials"], r["median_us"], r["std_us"]), (137.05, 600, 162.75, 44.04))

    def test_nested_rx_stage(self):
        r = find(self.rows, "ULSCH segments decoding time")
        self.assertEqual((r["mean_us"], r["trials"], r["depth"]), (35.00, 1800, 2))


class LdpctestTest(unittest.TestCase):
    rows = rows_of("physim.5g.ldpctest.test1.txt")

    def test_print_meas_columns(self):
        r = [x for x in self.rows if x["name"] == "ldpc_encoder"][0]
        self.assertEqual((r["mean_us"], r["max_us"], r["std_us"], r["trials"]), (211.574, 239.542, 8.237, 300))
        self.assertIsNone(r["snr"])

    def test_all_print_meas_lines(self):
        self.assertEqual(len(self.rows), 9)
        r = [x for x in self.rows if x["name"] == "cnProc (per iteration)"][0]
        self.assertEqual(r["trials"], 590)


class GbenchTest(unittest.TestCase):
    rows = parse_output.parse_gbench_json(os.path.join(DATA, "benchmark_rotate_vector.json"), test="b", run=1)

    def test_converted_to_us(self):
        self.assertEqual(len(self.rows), 6)
        r = self.rows[0]
        self.assertEqual(r["name"], "BM_rotate_cpx_vector/100")
        self.assertAlmostEqual(r["mean_us"], 18.249706869146515 / 1000)
        self.assertEqual(r["trials"], 38656814)


class GbenchConsoleIgnoredTest(unittest.TestCase):
    def test_console_table_not_parsed(self):
        # Benchmarks are read from JSON; the console table must not produce rows.
        self.assertEqual(rows_of("benchmark_rotate_vector.txt"), [])


class AggregateTest(unittest.TestCase):
    def test_cv_across_runs(self):
        rows = [{"test": "t", "snr": "1", "block": 0, "stage": "s", "name": "s", "section": "", "depth": 0,
                 "format": "printStat", "run": k, "mean_us": v, "trials": 10} for k, v in enumerate([9.0, 10.0, 11.0], 1)]
        e = aggregate.aggregate(rows)[0]
        self.assertEqual((e["n_runs"], e["mean_us"], e["min_us"], e["max_us"]), (3, 10.0, 9.0, 11.0))
        self.assertAlmostEqual(e["cv"], 0.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
