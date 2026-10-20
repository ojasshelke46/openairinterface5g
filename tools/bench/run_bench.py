#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-CSSL-1.0
"""Run existing OAI timed tests several times and summarise the timing they print.

Tests are run through ctest, so each one uses exactly the command line registered in
openair1/SIMULATION/tests/CMakeLists.txt. Google Benchmark executables are run directly
with --benchmark_out, because their ctest entry takes no arguments.

Output, under <out>/<hostname>/:
  manifest.json            machine and build description
  runs.json                exit code, duration and load average of every run
  <test>/run<k>.log        raw output of run k (stdout and stderr)
  <test>/run<k>.json       Google Benchmark JSON (benchmark tests only)
  rows.json                every parsed timing row
  summary.json/.csv/.md    aggregated across runs
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aggregate  # noqa: E402
import manifest  # noqa: E402
import parse_output  # noqa: E402

DEFAULT_TESTS = [
    # One test per kind, used as parser fixtures.
    "physim.5g.nr_dlsim.basic.test1",
    "physim.5g.nr_ulsim.misc.test1",
    "physim.5g.ldpctest.test1",
    "benchmark_rotate_vector",
    # Configurations that differ from a sibling test in one parameter (see README.md).
    "physim.5g.nr_dlsim.basic.test2",
    "physim.5g.nr_ulsim.misc.test5",
    "physim.5g.nr_dlsim.mcs.mimo.test1",
    "physim.5g.nr_ulsim.mimo.test2",
    "physim.5g.nr_ulsim.3gpp.test10",
    "physim.5g.nr_ulsim.3gpp.test19",
]

# Executables whose -P option has been checked to mean "print timing".
ALL_EXECUTABLES = ("nr_dlsim", "nr_ulsim")


def discover(build_dir):
    """Map ctest test name to its command (list of strings), using ctest -N -V."""
    out = subprocess.run(["ctest", "-N", "-V"], cwd=build_dir, capture_output=True, text=True, check=True).stdout
    names, commands = {}, {}
    for line in out.splitlines():
        m = re.match(r"^\s*Test\s+#(\d+): (\S+)", line)
        if m:
            names[m.group(1)] = m.group(2)
            continue
        m = re.match(r"^(\d+): Test command: (.*)$", line)
        if m:
            commands[m.group(1)] = m.group(2)
    tests = {}
    for num, name in names.items():
        cmd = commands.get(num, "")
        m = re.search(r'-DTEST_CMD=([^"]+)"', cmd)
        if m:
            tests[name] = m.group(1).split(";")
        else:
            tests[name] = cmd.split()
    return tests


def is_gbench(name, command):
    exe = os.path.basename(command[0]) if command else ""
    return name.startswith("benchmark_") or exe.startswith("benchmark_")


def select_all(tests):
    selected = []
    for name, cmd in tests.items():
        if not cmd:
            continue
        exe = os.path.basename(cmd[0])
        if exe in ALL_EXECUTABLES and "-P" in cmd[1:] and os.path.exists(cmd[0]):
            selected.append(name)
    return selected


def wait_for_idle(max_load, timeout):
    start = time.time()
    while True:
        load = manifest.load_average()
        if load is None or load["1min"] < max_load:
            return load, time.time() - start
        if time.time() - start > timeout:
            return load, None
        time.sleep(10)


def run_one(build_dir, name, command, run_dir, k):
    log = os.path.join(run_dir, "run{}.log".format(k))
    record = {"run": k, "log": log, "load_1min_before": (manifest.load_average() or {}).get("1min")}
    start = time.time()
    if is_gbench(name, command):
        jpath = os.path.join(run_dir, "run{}.json".format(k))
        cmd = [command[0], "--benchmark_out={}".format(jpath), "--benchmark_out_format=json"]
        cwd = os.path.dirname(command[0])
        record["json"] = jpath
    else:
        cmd = ["ctest", "-V", "-R", "^{}$".format(re.escape(name))]
        cwd = build_dir
    with open(log, "w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT)
    record["exit_code"] = proc.returncode
    record["seconds"] = round(time.time() - start, 2)
    record["command"] = cmd
    return record


def parse_runs(run_records):
    rows = []
    for name, recs in run_records.items():
        for r in recs:
            if r.get("json") and os.path.exists(r["json"]):
                rows.extend(parse_output.parse_gbench_json(r["json"], test=name, run=r["run"]))
            elif os.path.exists(r["log"]):
                rows.extend(parse_output.parse_file(r["log"], test=name, run=r["run"]))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-dir", required=True, help="cmake build directory configured with ENABLE_PHYSIM_TESTS=ON")
    ap.add_argument("--out", default="results", help="output root (default: results)")
    ap.add_argument("--tests", help="comma separated ctest test names (default: built in list)")
    ap.add_argument("--all", action="store_true", help="every timed nr_dlsim and nr_ulsim test (-P) that is built")
    ap.add_argument("--runs", type=int, default=10, help="repetitions per test (default: 10)")
    ap.add_argument("--max-load", type=float, default=1.0, help="wait until 1 min load average is below this")
    ap.add_argument("--idle-timeout", type=int, default=900, help="seconds to wait for idle before giving up")
    ap.add_argument("--no-idle-check", action="store_true", help="do not wait for a low load average")
    ap.add_argument("--force", action="store_true", help="rerun runs whose log already exists")
    ap.add_argument("--parse-only", action="store_true", help="do not run anything, only parse and aggregate")
    args = ap.parse_args()

    build_dir = os.path.abspath(args.build_dir)
    host = socket.gethostname()
    out_dir = os.path.join(os.path.abspath(args.out), host)
    os.makedirs(out_dir, exist_ok=True)
    runs_path = os.path.join(out_dir, "runs.json")
    run_records = {}
    if os.path.exists(runs_path) and not args.force:
        with open(runs_path, encoding="utf-8") as f:
            run_records = json.load(f)

    tests = discover(build_dir)
    if args.all:
        names = select_all(tests)
    elif args.tests:
        names = [t.strip() for t in args.tests.split(",") if t.strip()]
    else:
        names = list(DEFAULT_TESTS)
    missing = [n for n in names if n not in tests]
    if missing:
        sys.exit("unknown test(s): {}".format(", ".join(missing)))

    man_path = os.path.join(out_dir, "manifest.json")
    if not args.parse_only:
        man = manifest.collect(build_dir)
        if args.no_idle_check:
            load, waited = manifest.load_average(), 0
        else:
            print("waiting for 1 min load average < {} ...".format(args.max_load), flush=True)
            load, waited = wait_for_idle(args.max_load, args.idle_timeout)
            if waited is None:
                sys.exit("machine not idle after {} s (load {}); rerun later or use --no-idle-check".format(
                    args.idle_timeout, load))
        man["load_average_before_runs"] = load
        man["idle_wait_seconds"] = round(waited, 1)
        man["max_load"] = None if args.no_idle_check else args.max_load
        man["runs_per_test"] = args.runs
        man["tests"] = names
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(man, f, indent=2)

        # Interleave: run k of every test, then run k+1, so slow drift spreads over all tests.
        for k in range(1, args.runs + 1):
            for name in names:
                recs = run_records.setdefault(name, [])
                if any(r["run"] == k for r in recs) and not args.force:
                    continue
                recs[:] = [r for r in recs if r["run"] != k]
                run_dir = os.path.join(out_dir, name)
                os.makedirs(run_dir, exist_ok=True)
                rec = run_one(build_dir, name, tests[name], run_dir, k)
                recs.append(rec)
                recs.sort(key=lambda r: r["run"])
                print("{} run{} exit={} {}s".format(name, k, rec["exit_code"], rec["seconds"]), flush=True)
                with open(runs_path, "w", encoding="utf-8") as f:
                    json.dump(run_records, f, indent=2)

    with open(man_path, encoding="utf-8") as f:
        man = json.load(f)
    selected = {n: run_records.get(n, []) for n in names}
    rows = parse_runs(selected)
    with open(os.path.join(out_dir, "rows.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    entries = aggregate.aggregate(rows)
    aggregate.write_json(os.path.join(out_dir, "summary.json"), man, selected, entries)
    aggregate.write_csv(os.path.join(out_dir, "summary.csv"), entries)
    aggregate.write_markdown(os.path.join(out_dir, "summary.md"), man, selected, entries, man.get("runs_per_test"))
    print("wrote {}".format(out_dir))


if __name__ == "__main__":
    main()
