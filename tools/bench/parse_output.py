# SPDX-License-Identifier: LicenseRef-CSSL-1.0
"""Parse the timing lines that OAI test binaries already print.

Three formats are handled, all produced by existing code:
  * printDistribution / printStatIndent* (openair1/SIMULATION/LTE_PHY/common_sim.h),
    used by nr_dlsim and nr_ulsim with -P:
        "PHY proc tx                                  29.32 us (599 trials)"
        " Statistics std=7.85, min=25.58, q1=27.25, median=29.33, q3=29.67, max=53.04 us (on 599 trials)"
        "    |__ DLSCH encoding time                 19.65 us (599 trials)\t\t(11768.52 total [ms])"
    The "total [ms]" column is ignored: it prints diff/1000 with diff in ns, so it is in us.
  * print_meas (common/utils/time_meas.c), used by ldpctest, columns mean, max, std, trials:
        "             ldpc_encoder:          211.574 us;         239.542 us;           8.237 us;             300;"
  * Google Benchmark JSON written with --benchmark_out (benchmark_rotate_vector).

nr_dlsim and nr_ulsim print one timing block per SNR step. Each row carries the SNR of the
most recent "SNR <x>:" line and the block index (0 for the first SNR step).
depth is the print level: 0 for printDistribution lines, 1, 2, 3 for printStatIndent, 2, 3.
"""

import json
import re

CTEST_PREFIX = re.compile(r"^\d+: ")
SNR_LINE = re.compile(r"^SNR (?P<snr>-?[0-9]+(?:\.[0-9]+)?):")
SECTION_LINE = re.compile(r"^(?P<section>UE TX|UE RX|gNB TX|gNB RX)\s*$")
STAT_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<tree>\|__\s+)?(?P<name>\S.*?)\s+(?P<mean>-?[0-9]+(?:\.[0-9]+)?) us \(\s*(?P<trials>\d+) trials\)"
)
STATISTICS_LINE = re.compile(
    r"^\s*Statistics std=(?P<std>-?[0-9.]+), min=(?P<min>-?[0-9.]+), q1=(?P<q1>-?[0-9.]+), "
    r"median=(?P<median>-?[0-9.]+), q3=(?P<q3>-?[0-9.]+), max=(?P<max>-?[0-9.]+) \S+ \(on (?P<trials>\d+) trials\)"
)
PRINT_MEAS_LINE = re.compile(
    r"^\s*(?P<name>\S.*?):\s+(?P<mean>[0-9.]+) us;\s+(?P<max>[0-9.]+) us;\s+(?P<std>[0-9.]+) us;\s+(?P<trials>\d+);"
)

TIME_UNIT_TO_US = {"ns": 1e-3, "us": 1.0, "ms": 1e3, "s": 1e6}


def _strip_ctest_prefix(line):
    return CTEST_PREFIX.sub("", line, count=1)


def parse_text(text, test="", run=None, log=""):
    """Return a list of row dicts for one run log (ctest -V output or raw binary output)."""
    rows = []
    section = ""
    snr = None
    block = -1
    seen_snr = []
    keys_in_block = {}
    last_stat_row = None

    for raw in text.splitlines():
        line = _strip_ctest_prefix(raw.rstrip("\n"))

        m = SNR_LINE.match(line)
        if m:
            value = m.group("snr")
            if value not in seen_snr:
                seen_snr.append(value)
                block = len(seen_snr) - 1
                keys_in_block = {}
                section = ""
            snr = value
            continue

        m = SECTION_LINE.match(line)
        if m:
            section = m.group("section")
            continue

        m = STATISTICS_LINE.match(line)
        if m:
            if last_stat_row is not None:
                for field in ("std", "min", "q1", "median", "q3", "max"):
                    last_stat_row[field + "_us"] = float(m.group(field))
                last_stat_row["stat_trials"] = int(m.group("trials"))
            continue

        m = PRINT_MEAS_LINE.match(line)
        if m:
            name = m.group("name").strip()
            rows.append(
                {
                    "test": test,
                    "run": run,
                    "log": log,
                    "format": "print_meas",
                    "section": "",
                    "snr": None,
                    "block": None,
                    "stage": name,
                    "name": name,
                    "depth": 0,
                    "mean_us": float(m.group("mean")),
                    "trials": int(m.group("trials")),
                    "max_us": float(m.group("max")),
                    "std_us": float(m.group("std")),
                }
            )
            last_stat_row = None
            continue

        m = STAT_LINE.match(line)
        if m:
            name = m.group("name").strip()
            depth = 0 if not m.group("tree") else len(m.group("indent")) // 4 + 1
            key = "{}|{}|{}".format(section, depth, name)
            count = keys_in_block.get(key, 0) + 1
            keys_in_block[key] = count
            stage = key if count == 1 else "{}#{}".format(key, count)
            row = {
                "test": test,
                "run": run,
                "log": log,
                "format": "printStat",
                "section": section,
                "snr": snr,
                "block": block if block >= 0 else None,
                "stage": stage,
                "name": name,
                "depth": depth,
                "mean_us": float(m.group("mean")),
                "trials": int(m.group("trials")),
            }
            rows.append(row)
            last_stat_row = row
            continue

    return rows


def parse_file(path, test="", run=None):
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_text(f.read(), test=test, run=run, log=str(path))


def parse_gbench_json(path, test="", run=None):
    """Return rows from a Google Benchmark --benchmark_out JSON file (iteration runs only)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    for b in data.get("benchmarks", []):
        if b.get("run_type", "iteration") != "iteration":
            continue
        scale = TIME_UNIT_TO_US[b.get("time_unit", "ns")]
        rows.append(
            {
                "test": test,
                "run": run,
                "log": str(path),
                "format": "gbench",
                "section": "",
                "snr": None,
                "block": None,
                "stage": b["name"],
                "name": b["name"],
                "depth": 0,
                "mean_us": b["real_time"] * scale,
                "cpu_us": b["cpu_time"] * scale,
                "trials": int(b["iterations"]),
            }
        )
    return rows
