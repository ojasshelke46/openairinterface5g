# SPDX-License-Identifier: LicenseRef-CSSL-1.0
"""Describe the machine and the build a benchmark ran on. Linux only, no external dependencies."""

import datetime
import os
import platform
import re
import shutil
import socket
import subprocess


def _run(cmd, cwd=None):
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return out.returncode, out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, ""


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def load_average():
    text = _read("/proc/loadavg")
    if not text:
        return None
    one, five, fifteen = text.split()[:3]
    return {"1min": float(one), "5min": float(five), "15min": float(fifteen)}


def _lscpu():
    rc, out = _run(["lscpu"])
    info = {}
    if rc == 0:
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip()] = v.strip()
    return info


def _virtualization(lscpu):
    if shutil.which("systemd-detect-virt"):
        _, out = _run(["systemd-detect-virt"])
        if out:
            return out
    return lscpu.get("Hypervisor vendor", "unknown")


def _distro():
    text = _read("/etc/os-release") or ""
    m = re.search(r'^PRETTY_NAME="?([^"\n]*)"?', text, re.M)
    return m.group(1) if m else platform.platform()


def _cmake_cache(build_dir):
    cache = {}
    text = _read(os.path.join(build_dir, "CMakeCache.txt")) or ""
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_]+):[A-Z]+=(.*)$", line)
        if m:
            cache[m.group(1)] = m.group(2)
    return cache


def _build_type_and_flags(build_dir, cache):
    """Build type and C flags as actually used, read from build.ninja (or Makefiles flags.make)."""
    build_type = cache.get("CMAKE_BUILD_TYPE") or None
    flags = None
    ninja = _read(os.path.join(build_dir, "build.ninja"))
    if ninja:
        # Prefer the flags of the nr_dlsim main object, fall back to the first C object.
        m = re.search(r"^build [^\n]*dlsim\.c\.o: C_COMPILER[^\n]*\n(?:  [^\n]*\n)*?  FLAGS = ([^\n]*)", ninja, re.M)
        if not m:
            m = re.search(r"^build [^\n]*\.c\.o: C_COMPILER[^\n]*\n(?:  [^\n]*\n)*?  FLAGS = ([^\n]*)", ninja, re.M)
        if m:
            flags = m.group(1).strip()
        if not build_type:
            m = re.search(r'-DCMAKE_BUILD_TYPE=\\"([A-Za-z]+)\\"', ninja)
            if m:
                build_type = m.group(1)
    return build_type, flags


def _compiler(cache):
    path = cache.get("CMAKE_C_COMPILER")
    if not path:
        return None
    _, out = _run([path, "--version"])
    return {"path": path, "version": out.splitlines()[0] if out else None}


def _git(source_dir):
    if not source_dir:
        return None
    rc, sha = _run(["git", "-C", source_dir, "rev-parse", "HEAD"])
    if rc != 0:
        return None
    _, dirty = _run(["git", "-C", source_dir, "status", "--porcelain", "--untracked-files=no"])
    return {"sha": sha, "tracked_changes": bool(dirty)}


def collect(build_dir):
    lscpu = _lscpu()
    cache = _cmake_cache(build_dir)
    build_type, flags = _build_type_and_flags(build_dir, cache)
    meminfo = _read("/proc/meminfo") or ""
    m = re.search(r"^MemTotal:\s+(\d+) kB", meminfo, re.M)
    return {
        "host": socket.gethostname(),
        "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "cpu": {
            "architecture": lscpu.get("Architecture", platform.machine()),
            "vendor": lscpu.get("Vendor ID"),
            "model_name": lscpu.get("Model name"),
            "cpus_lscpu": lscpu.get("CPU(s)"),
            "cpus_usable": len(os.sched_getaffinity(0)),
            "governor": _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        },
        "memory_kb": int(m.group(1)) if m else None,
        "virtualization": _virtualization(lscpu),
        "kernel": platform.release(),
        "distro": _distro(),
        "build": {
            "dir": os.path.abspath(build_dir),
            "source_dir": cache.get("CMAKE_HOME_DIRECTORY"),
            "build_type": build_type,
            "c_flags": flags,
            "compiler": _compiler(cache),
        },
        "git": _git(cache.get("CMAKE_HOME_DIRECTORY")),
        "python": platform.python_version(),
        "load_average_at_collect": load_average(),
    }
