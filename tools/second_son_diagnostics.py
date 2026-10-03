#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Diagnostic, verification, and telemetry toolkit for inFAMOUS: Second Son on shadPS4
(Intel Core i5-12450H + NVIDIA GeForce RTX 3050 Laptop GPU 4 GB VRAM).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

SECOND_SON_SERIALS: tuple[str, ...] = (
    "CUSA00004",  # Europe / Australia
    "CUSA00223",  # North America
    "CUSA00046",  # Asia
    "CUSA00359",  # Japan
)

FIRST_LIGHT_SERIALS: tuple[str, ...] = (
    "CUSA00575",  # North America
    "CUSA00897",  # Europe
)

ALL_INFAMOUS_SERIALS: tuple[str, ...] = SECOND_SON_SERIALS + FIRST_LIGHT_SERIALS

GB = 1024 * 1024 * 1024
MB = 1024 * 1024
KB = 1024

TARGET_GC_THRESHOLD = 8 * GB
DEFAULT_PRESSURE_GC_MEMORY = 1 * GB
DEFAULT_CRITICAL_GC_MEMORY = 2 * GB


@dataclass
class VramGcThresholds:
    device_local_bytes: int
    trigger_gc_bytes: int
    pressure_gc_bytes: int
    critical_gc_bytes: int
    normal_ticks_to_destroy: int
    pressured_ticks_to_destroy: int
    aggressive_ticks_to_destroy: int


@dataclass
class LogDiagnosis:
    has_out_of_device_memory: bool = False
    has_bc_format_unsupported: bool = False
    has_guarded_page_fread_einval: bool = False
    has_compat_profile_active: bool = False
    has_redzone_patch_active: bool = False
    has_vram_spillover_triggered: bool = False
    unsupported_formats: list[str] = field(default_factory=list)
    unimplemented_symbols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class GameDumpStatus:
    found: bool
    path: str | None = None
    serial: str | None = None
    title: str | None = None
    app_ver: str | None = None
    eboot_exists: bool = False
    eboot_format: str | None = None
    modules_found: list[str] = field(default_factory=list)


def compute_stock_vram_gc_thresholds(device_local_bytes: int) -> VramGcThresholds:
    """Reproduces the stock upstream shadPS4 (8e23388) TextureCache GC calculation."""
    min_spacing_expected = device_local_bytes - 1 * GB
    min_spacing_critical = device_local_bytes - 512 * MB
    mem_threshold = min(device_local_bytes, TARGET_GC_THRESHOLD)
    min_vacancy_expected = (6 * mem_threshold) // 10
    min_vacancy_critical = (2 * mem_threshold) // 10
    min_pressure_floor = max(256 * MB, min(device_local_bytes // 4, DEFAULT_PRESSURE_GC_MEMORY))
    min_critical_floor = max(512 * MB, min(device_local_bytes // 2, DEFAULT_CRITICAL_GC_MEMORY))

    pressure_gc = max(
        min(device_local_bytes - min_vacancy_expected, min_spacing_expected),
        min_pressure_floor,
    )
    critical_gc = max(
        min(device_local_bytes - min_vacancy_critical, min_spacing_critical),
        min_critical_floor,
    )
    trigger_gc = (device_local_bytes - mem_threshold) // 2

    # Stock inverted ticks_to_destroy
    return VramGcThresholds(
        device_local_bytes=device_local_bytes,
        trigger_gc_bytes=trigger_gc,
        pressure_gc_bytes=pressure_gc,
        critical_gc_bytes=critical_gc,
        normal_ticks_to_destroy=16,
        pressured_ticks_to_destroy=80,
        aggressive_ticks_to_destroy=160,
    )


def compute_fixed_vram_gc_thresholds(device_local_bytes: int) -> VramGcThresholds:
    """Computes the corrected TextureCache GC thresholds and eviction ages for <=8 GB GPUs."""
    min_spacing_expected = device_local_bytes - 1 * GB
    min_spacing_critical = device_local_bytes - 512 * MB
    mem_threshold = min(device_local_bytes, TARGET_GC_THRESHOLD)
    min_vacancy_expected = (6 * mem_threshold) // 10
    min_vacancy_critical = (2 * mem_threshold) // 10
    min_pressure_floor = max(256 * MB, min(device_local_bytes // 4, DEFAULT_PRESSURE_GC_MEMORY))
    min_critical_floor = max(512 * MB, min(device_local_bytes // 2, DEFAULT_CRITICAL_GC_MEMORY))

    pressure_gc = max(
        min(device_local_bytes - min_vacancy_expected, min_spacing_expected),
        min_pressure_floor,
    )
    critical_gc = max(
        min(device_local_bytes - min_vacancy_critical, min_spacing_critical),
        min_critical_floor,
    )
    raw_trigger = (device_local_bytes - mem_threshold) // 2
    trigger_gc = (
        raw_trigger
        if raw_trigger > 0
        else max(min_pressure_floor // 2, (pressure_gc * 3) // 4)
    )

    return VramGcThresholds(
        device_local_bytes=device_local_bytes,
        trigger_gc_bytes=trigger_gc,
        pressure_gc_bytes=pressure_gc,
        critical_gc_bytes=critical_gc,
        normal_ticks_to_destroy=160,
        pressured_ticks_to_destroy=32,
        aggressive_ticks_to_destroy=4,
    )


def parse_param_sfo_bytes(data: bytes) -> dict[str, str | int]:
    """Parses a binary PlayStation 4 param.sfo (PSF) file into a dictionary."""
    if len(data) < 20:
        raise ValueError("param.sfo buffer is shorter than 20-byte PSF header")
    magic, version, key_table_offset, data_table_offset, num_entries = struct.unpack_from(
        "<4sIIII", data, 0
    )
    if magic != b"\x00PSF":
        raise ValueError(f"Invalid PSF magic: {magic!r}")

    result: dict[str, str | int] = {}
    for i in range(num_entries):
        entry_offset = 20 + i * 16
        if entry_offset + 16 > len(data):
            break
        key_off, param_fmt, param_len, param_max_len, data_off = struct.unpack_from(
            "<HHIII", data, entry_offset
        )
        abs_key_off = key_table_offset + key_off
        key_end = data.find(b"\x00", abs_key_off)
        if key_end == -1:
            continue
        key = data[abs_key_off:key_end].decode("utf-8", errors="replace")

        abs_data_off = data_table_offset + data_off
        raw_val = data[abs_data_off : abs_data_off + param_len]
        if param_fmt in (0x0204, 0x0004):  # UTF-8 string
            result[key] = raw_val.rstrip(b"\x00").decode("utf-8", errors="replace")
        elif param_fmt == 0x0404 and len(raw_val) == 4:  # uint32
            result[key] = struct.unpack("<I", raw_val)[0]
    return result


def build_synthetic_param_sfo(entries: dict[str, str | int]) -> bytes:
    """Constructs a valid binary PS4 param.sfo image for unit testing."""
    sorted_keys = sorted(entries.keys())
    key_table = bytearray()
    data_table = bytearray()
    index_entries: list[tuple[int, int, int, int, int]] = []

    for k in sorted_keys:
        key_off = len(key_table)
        key_table.extend(k.encode("utf-8") + b"\x00")
        val = entries[k]
        data_off = len(data_table)
        if isinstance(val, int):
            param_fmt = 0x0404
            encoded = struct.pack("<I", val)
            param_len = 4
            param_max_len = 4
        else:
            param_fmt = 0x0204
            encoded = val.encode("utf-8") + b"\x00"
            param_len = len(encoded)
            param_max_len = (param_len + 3) & ~3
            encoded = encoded.ljust(param_max_len, b"\x00")
        data_table.extend(encoded)
        index_entries.append((key_off, param_fmt, param_len, param_max_len, data_off))

    while len(key_table) % 4 != 0:
        key_table.append(0)

    header_size = 20 + 16 * len(index_entries)
    key_table_offset = header_size
    data_table_offset = key_table_offset + len(key_table)

    out = bytearray(
        struct.pack(
            "<4sIIII",
            b"\x00PSF",
            0x00000101,
            key_table_offset,
            data_table_offset,
            len(index_entries),
        )
    )
    for item in index_entries:
        out.extend(struct.pack("<HHIII", *item))
    out.extend(key_table)
    out.extend(data_table)
    return bytes(out)


def analyze_shadps4_log(log_text: str) -> LogDiagnosis:
    """Parses a shadPS4 log file for inFAMOUS: Second Son error signatures and active fixes."""
    diag = LogDiagnosis()
    for line in log_text.splitlines():
        if "ErrorOutOfDeviceMemory" in line or "VK_ERROR_OUT_OF_DEVICE_MEMORY" in line:
            diag.has_out_of_device_memory = True
            diag.errors.append(line.strip())
        if "is not supported (flags" in line and "image format" in line:
            diag.has_bc_format_unsupported = True
            m = re.search(r"image format (\S+) type (\S+) is not supported", line)
            if m:
                diag.unsupported_formats.append(f"{m.group(1)}:{m.group(2)}")
            diag.errors.append(line.strip())
        if "Failed to read file, error = Invalid argument" in line:
            diag.has_guarded_page_fread_einval = True
            diag.errors.append(line.strip())
        if "Activated inFAMOUS Sucker Punch Engine compatibility profile" in line:
            diag.has_compat_profile_active = True
        if "Enabled Windows guest red-zone static instruction patching" in line:
            diag.has_redzone_patch_active = True
        if "succeeded via unbudgeted VMA spillover" in line:
            diag.has_vram_spillover_triggered = True
        m_stub = re.search(r"Stub:\s+(\S+)\s+\(nid:", line)
        if m_stub:
            sym = m_stub.group(1)
            if sym not in diag.unimplemented_symbols:
                diag.unimplemented_symbols.append(sym)
    return diag


def inspect_game_dump(search_dirs: list[Path]) -> GameDumpStatus:
    """Scans candidate directories for a legitimate decrypted dump of inFAMOUS: Second Son."""
    for base in search_dirs:
        if not base.exists():
            continue
        candidates = [base]
        for serial in ALL_INFAMOUS_SERIALS:
            candidates.append(base / serial)
        try:
            for child in base.iterdir():
                if child.is_dir() and child not in candidates:
                    candidates.append(child)
        except OSError:
            pass

        for folder in candidates:
            sfo_path = folder / "sce_sys" / "param.sfo"
            eboot_path = folder / "eboot.bin"
            if not sfo_path.is_file() and not eboot_path.is_file():
                continue
            serial = None
            title = None
            app_ver = None
            if sfo_path.is_file():
                try:
                    sfo = parse_param_sfo_bytes(sfo_path.read_bytes())
                    serial = str(sfo.get("TITLE_ID", ""))
                    title = str(sfo.get("TITLE", ""))
                    app_ver = str(sfo.get("APP_VER", ""))
                except Exception:
                    pass
            if serial and serial not in ALL_INFAMOUS_SERIALS:
                if not title or (
                    "second son" not in title.lower() and "infamous" not in title.lower()
                ):
                    continue

            eboot_exists = eboot_path.is_file()
            eboot_format = None
            if eboot_exists:
                head = eboot_path.read_bytes()[:4]
                if head == b"\x7fELF":
                    eboot_format = "Decrypted ELF"
                elif head == b"\x4f\x15\x3d\x1d":
                    eboot_format = "Signed SELF"
                else:
                    eboot_format = f"Unknown ({head.hex()})"

            modules_dir = folder / "sce_module"
            modules = []
            if modules_dir.is_dir():
                modules = sorted(p.name for p in modules_dir.glob("*.prx"))

            return GameDumpStatus(
                found=True,
                path=str(folder.resolve()),
                serial=serial or folder.name,
                title=title,
                app_ver=app_ver,
                eboot_exists=eboot_exists,
                eboot_format=eboot_format,
                modules_found=modules,
            )

    return GameDumpStatus(found=False)


def collect_hardware_profile() -> dict[str, Any]:
    """Collects live host CPU, RAM, OS, and NVIDIA GPU telemetry."""
    vm = psutil.virtual_memory()
    profile: dict[str, Any] = {
        "os": platform.platform(),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "ram_total_mib": vm.total // MB,
        "ram_available_mib": vm.available // MB,
        "gpu_name": None,
        "gpu_vram_total_mib": None,
        "gpu_vram_free_mib": None,
        "gpu_driver_version": None,
    }
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            parts = [p.strip() for p in proc.stdout.strip().splitlines()[0].split(",")]
            if len(parts) >= 4:
                profile["gpu_name"] = parts[0]
                profile["gpu_vram_total_mib"] = int(parts[1])
                profile["gpu_vram_free_mib"] = int(parts[2])
                profile["gpu_driver_version"] = parts[3]
    except Exception:
        pass
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(
        description="inFAMOUS: Second Son shadPS4 compatibility & hardware diagnostic tool"
    )
    parser.add_argument(
        "--check-all",
        action="store_true",
        help="Run hardware audit, config check, and game dump search",
    )
    parser.add_argument(
        "--analyze-log",
        type=Path,
        help="Analyze a shadPS4 log file for inFAMOUS: Second Son issues",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if args.analyze_log:
        text = args.analyze_log.read_text(encoding="utf-8", errors="replace")
        print(json.dumps(asdict(analyze_shadps4_log(text)), indent=2))
        return 0

    hw = collect_hardware_profile()
    stock_gc = compute_stock_vram_gc_thresholds(3258 * MB)
    fixed_gc = compute_fixed_vram_gc_thresholds(3258 * MB)
    appdata = Path(os.environ.get("APPDATA", "")) / "shadPS4"
    dump = inspect_game_dump(
        [
            Path("C:/games/ps4"),
            repo_root / "games",
            repo_root / "game",
            appdata / "games",
        ]
    )

    report = {
        "hardware": hw,
        "vram_gc_comparison_3258mib_budget": {
            "stock": asdict(stock_gc),
            "fixed": asdict(fixed_gc),
        },
        "game_dump": asdict(dump),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
