# SPDX-License-Identifier: LicenseRef-CSSL-1.0
"""Aggregate parsed rows across repeated runs into summary.json, summary.csv and summary.md."""

import csv
import json
import statistics

SCHEMA_VERSION = 1


def _stats(values):
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "n_runs": len(values),
        "mean_us": mean,
        "min_us": min(values),
        "max_us": max(values),
        "stdev_us": stdev,
        "cv": stdev / mean if mean > 0 else None,
    }


def aggregate(rows):
    """Group rows by test, SNR block and stage; one value per run (the mean the binary printed)."""
    groups = {}
    order = {}
    for r in rows:
        key = (r["test"], r["snr"], r["block"], r["stage"])
        if key not in groups:
            groups[key] = []
            order[key] = len(order)
        groups[key].append(r)

    out = []
    for key in sorted(groups, key=lambda k: order[k]):
        test, snr, block, stage = key
        rs = groups[key]
        entry = {
            "test": test,
            "snr": snr,
            "block": block,
            "stage": stage,
            "name": rs[0]["name"],
            "section": rs[0]["section"],
            "depth": rs[0]["depth"],
            "format": rs[0]["format"],
            "runs": sorted(r["run"] for r in rs),
            "trials_min": min(r["trials"] for r in rs),
            "trials_max": max(r["trials"] for r in rs),
        }
        entry.update(_stats([r["mean_us"] for r in rs]))
        stds = [r["std_us"] for r in rs if "std_us" in r]
        if stds:
            entry["binary_std_us_mean"] = statistics.fmean(stds)
        medians = [r["median_us"] for r in rs if "median_us" in r]
        if medians:
            entry["binary_median_us_mean"] = statistics.fmean(medians)
        out.append(entry)
    return out


def write_json(path, manifest, run_records, entries):
    tests = {}
    for e in entries:
        t = tests.setdefault(e["test"], {"blocks": {}})
        bkey = "none" if e["snr"] is None else e["snr"]
        t["blocks"].setdefault(bkey, {"snr": e["snr"], "block": e["block"], "stages": []})["stages"].append(e)
    for name, rec in run_records.items():
        tests.setdefault(name, {"blocks": {}})["runs"] = rec
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"schema_version": SCHEMA_VERSION, "host": manifest.get("host"), "git": manifest.get("git"), "tests": tests},
            f,
            indent=2,
        )


CSV_FIELDS = [
    "test", "snr", "block", "section", "depth", "stage", "name", "format", "n_runs",
    "mean_us", "min_us", "max_us", "stdev_us", "cv", "trials_min", "trials_max",
    "binary_std_us_mean", "binary_median_us_mean",
]


def write_csv(path, entries):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for e in entries:
            w.writerow(e)


def _first_block(entries_for_test):
    """Entries of the first SNR block (block 0), or block-less entries (ldpctest, benchmarks)."""
    blocks = [e["block"] for e in entries_for_test if e["block"] is not None]
    if not blocks:
        return entries_for_test
    first = min(blocks)
    return [e for e in entries_for_test if e["block"] == first]


def _fmt(x, digits=2):
    return "" if x is None else "{:.{}f}".format(x, digits)


def write_markdown(path, manifest, run_records, entries, n_runs):
    lines = []
    git = manifest.get("git") or {}
    cpu = manifest.get("cpu", {})
    build = manifest.get("build", {})
    load = manifest.get("load_average_before_runs") or {}
    lines.append("# Timing summary")
    lines.append("")
    lines.append("- Host: {} ({}, {}, virtualization: {})".format(
        manifest.get("host"), cpu.get("architecture"), manifest.get("distro"), manifest.get("virtualization")))
    lines.append("- CPU: vendor {}, model {}, {} usable CPUs, governor {}".format(
        cpu.get("vendor"), cpu.get("model_name"), cpu.get("cpus_usable"), cpu.get("governor")))
    lines.append("- Commit: {}{}".format(git.get("sha"), " (with local changes)" if git.get("tracked_changes") else ""))
    lines.append("- Build: {}, {}".format(build.get("build_type"), (build.get("compiler") or {}).get("version")))
    lines.append("- Runs per test: {}. 1 min load average before the runs: {}".format(n_runs, load.get("1min")))
    lines.append("- Date (UTC): {}".format(manifest.get("date_utc")))
    lines.append("")
    lines.append("How to read this:")
    lines.append("")
    lines.append("- mean is the average, across runs, of the mean each binary printed. min and max are the lowest")
    lines.append("  and highest of those per run means. CV is the standard deviation across runs divided by the mean.")
    lines.append("- nr_dlsim and nr_ulsim print one timing block per SNR step. This file shows only the first SNR")
    lines.append("  block, because some counters are not reset between SNR steps and only the first block starts")
    lines.append("  from zero for every counter. All blocks are in summary.json and summary.csv.")
    lines.append("- Stages with 0 trials are left out. The \"total [ms]\" column printed by the binaries is ignored.")
    lines.append("- The section label (UE TX, gNB RX) is the last header printed before the line. In nr_ulsim,")
    lines.append("  \"Multipath Channel (CPU)\" and \"Add Noise (CPU)\" follow the gNB RX header, but they are the")
    lines.append("  simulator's channel model, not gNB processing. The same two stages appear in nr_dlsim.")
    lines.append("- Stages that print 0.000 us with a nonzero trial count are counted but record no measurable time.")
    lines.append("")

    failed = {t: [r for r in recs if r["exit_code"] != 0] for t, recs in run_records.items()}
    tests_in_order = list(dict.fromkeys([e["test"] for e in entries] + list(run_records)))
    for test in tests_in_order:
        recs = run_records.get(test, [])
        ok = sum(1 for r in recs if r["exit_code"] == 0)
        lines.append("## {}".format(test))
        lines.append("")
        if recs:
            lines.append("Runs: {} of {} exited 0.".format(ok, len(recs)))
        if failed.get(test):
            lines.append("Failed runs: {}.".format(", ".join(
                "run{} (exit {}, log {})".format(r["run"], r["exit_code"], r["log"]) for r in failed[test])))
        rows = [e for e in entries if e["test"] == test]
        rows = _first_block(rows)
        rows = [e for e in rows if e["trials_max"] > 0]
        if not rows:
            lines.append("")
            lines.append("No timing rows parsed.")
            lines.append("")
            continue
        snr = rows[0]["snr"]
        if snr is not None:
            lines.append("First SNR block: SNR {}.".format(snr))
        lines.append("")
        lines.append("| stage | mean us | min us | max us | CV % | runs | trials |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for e in rows:
            label = e["name"]
            if e["section"]:
                label = "{}: {}".format(e["section"], label)
            if e["depth"]:
                label = "{}{}".format("&nbsp;&nbsp;" * e["depth"], label)
            trials = str(e["trials_min"]) if e["trials_min"] == e["trials_max"] else "{} to {}".format(e["trials_min"], e["trials_max"])
            cv = None if e["cv"] is None else e["cv"] * 100
            digits = 3 if e["mean_us"] < 1 else 2
            lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                label, _fmt(e["mean_us"], digits), _fmt(e["min_us"], digits), _fmt(e["max_us"], digits),
                _fmt(cv, 1), e["n_runs"], trials))
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
