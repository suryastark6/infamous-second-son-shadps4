# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Automated regression and verification test suite for the inFAMOUS: Second Son
shadPS4 compatibility, VRAM management, and configuration engineering changes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import second_son_diagnostics as diag  # noqa: E402


def test_infamous_serial_constants():
    assert "CUSA00004" in diag.SECOND_SON_SERIALS
    assert "CUSA00223" in diag.SECOND_SON_SERIALS
    assert "CUSA00046" in diag.SECOND_SON_SERIALS
    assert "CUSA00359" in diag.SECOND_SON_SERIALS
    assert "CUSA00575" in diag.FIRST_LIGHT_SERIALS
    assert "CUSA00897" in diag.FIRST_LIGHT_SERIALS


def test_vram_gc_thresholds_4gb_rtx3050():
    # On RTX 3050 4 GB Laptop GPU, VMA reports ~3258 MiB budget (80% of 4096 MiB)
    budget_bytes = 3258 * diag.MB
    stock = diag.compute_stock_vram_gc_thresholds(budget_bytes)
    fixed = diag.compute_fixed_vram_gc_thresholds(budget_bytes)

    # Stock bug #1: trigger_gc_bytes was 0 on any GPU <= 8 GB
    assert stock.trigger_gc_bytes == 0
    assert fixed.trigger_gc_bytes > 512 * diag.MB
    assert fixed.trigger_gc_bytes < fixed.pressure_gc_bytes
    assert fixed.pressure_gc_bytes < fixed.critical_gc_bytes

    # Stock bug #2: ticks_to_destroy was inverted (160 under aggressive pressure vs 16 normal)
    assert stock.aggressive_ticks_to_destroy > stock.normal_ticks_to_destroy
    assert fixed.aggressive_ticks_to_destroy < fixed.pressured_ticks_to_destroy
    assert fixed.pressured_ticks_to_destroy < fixed.normal_ticks_to_destroy
    assert fixed.aggressive_ticks_to_destroy == 4
    assert fixed.normal_ticks_to_destroy == 160


def test_synthetic_param_sfo_roundtrip():
    raw = diag.build_synthetic_param_sfo(
        {
            "TITLE_ID": "CUSA00004",
            "TITLE": "inFAMOUS Second Son",
            "APP_VER": "01.07",
            "SYSTEM_VER": 0x01700000,
        }
    )
    parsed = diag.parse_param_sfo_bytes(raw)
    assert parsed["TITLE_ID"] == "CUSA00004"
    assert parsed["TITLE"] == "inFAMOUS Second Son"
    assert parsed["APP_VER"] == "01.07"
    assert parsed["SYSTEM_VER"] == 0x01700000


def test_log_analyzer_detects_second_son_signatures():
    sample_log = """
[Render.Vulkan] <Error> image.cpp:167 Image: image format Bc3UnormBlock type e2D is not supported (flags MutableFormat | ExtendedUsage | BlockTexelViewCompatible, usage TransferSrc | TransferDst | Sampled | Storage)
[Render.Vulkan] <Error> image.cpp:167 Image: image format Bc5UnormBlock type e2D is not supported (flags MutableFormat | ExtendedUsage | BlockTexelViewCompatible, usage TransferSrc | TransferDst | Sampled | Storage)
[Debug] <Critical> buffer.cpp:104 operator(): Assertion Failed! Failed allocating buffer with error ErrorOutOfDeviceMemory
[Common.Filesystem] <Critical> io_file.h:174 ReadRaw: Assertion Failed! Failed to read file, error = Invalid argument
[Compatibility] <Info> game_compatibility.cpp:103 OnGameBoot: [inFAMOUS Compatibility] Enabled Windows guest red-zone static instruction patching for inFAMOUS Second Son (CUSA00004)
[Compatibility] <Info> game_compatibility.cpp:122 OnGameBoot: Activated inFAMOUS Sucker Punch Engine compatibility profile for 'inFAMOUS Second Son' [CUSA00004 v01.07]
[Performance] <Warning> image.cpp:134 Create: VRAM budget exceeded during 2048x2048 image creation (Bc3UnormBlock); succeeded via unbudgeted VMA spillover
[Core.Linker] <Info> module.cpp:210 Resolve: Stub: sceVoiceQoSInit (nid: abcdef123456)
"""
    res = diag.analyze_shadps4_log(sample_log)
    assert res.has_out_of_device_memory is True
    assert res.has_bc_format_unsupported is True
    assert "Bc3UnormBlock:e2D" in res.unsupported_formats
    assert "Bc5UnormBlock:e2D" in res.unsupported_formats
    assert res.has_guarded_page_fread_einval is True
    assert res.has_compat_profile_active is True
    assert res.has_redzone_patch_active is True
    assert res.has_vram_spillover_triggered is True
    assert "sceVoiceQoSInit" in res.unimplemented_symbols


def test_custom_config_profiles_valid_and_complete():
    cfg_dir = REPO_ROOT / "config" / "custom_configs"
    for serial in diag.ALL_INFAMOUS_SERIALS:
        cfg_path = cfg_dir / f"{serial}.json"
        assert cfg_path.is_file(), f"Missing custom config for {serial}"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["General"]["redzone_patches"] is True
        assert data["General"]["second_son_compat_enabled"] is True
        assert data["General"]["motion_shake_fallback_enabled"] is True
        assert data["GPU"]["readbacks_mode"] == 1
        assert data["GPU"]["adaptive_readbacks_enabled"] is True
        assert data["GPU"]["vram_spillover_enabled"] is True
        assert data["GPU"]["compressed_storage_fallback_enabled"] is True
        assert data["Vulkan"]["gpu_id"] == 0
        assert data["Vulkan"]["pipeline_cache_enabled"] is True


def test_cpp_source_patches_present():
    # Verify all 6 C++ subsystem modifications are intact in the repository source tree
    compat_h = (REPO_ROOT / "src/core/game_compatibility.h").read_text(encoding="utf-8")
    compat_cpp = (REPO_ROOT / "src/core/game_compatibility.cpp").read_text(encoding="utf-8")
    assert "class GameCompatibilityManager" in compat_h
    assert "IsInfamousSecondSonSerial" in compat_cpp

    tex_cache = (REPO_ROOT / "src/video_core/texture_cache/texture_cache.cpp").read_text(
        encoding="utf-8"
    )
    assert "ticks_to_destroy = aggresive ? 4 : pressured ? 32 : 160;" in tex_cache

    image_cpp = (REPO_ROOT / "src/video_core/texture_cache/image.cpp").read_text(encoding="utf-8")
    assert "usage_flags &= ~vk::ImageUsageFlagBits::eStorage;" in image_cpp
    assert "IsVramSpilloverEnabled()" in image_cpp

    buf_cache_cpp = (REPO_ROOT / "src/video_core/buffer_cache/buffer_cache.cpp").read_text(
        encoding="utf-8"
    )
    assert "GetEffectiveReadbackMaxRangeBytes" in buf_cache_cpp
    assert "fallback_arena_memory_type_index" in buf_cache_cpp

    io_file_h = (REPO_ROOT / "src/common/io_file.h").read_text(encoding="utf-8")
    assert "std::clearerr(file);" in io_file_h

    controller_cpp = (REPO_ROOT / "src/input/controller.cpp").read_text(encoding="utf-8")
    assert "ShouldSynthesizeSprayCanMotion()" in controller_cpp


def test_host_hardware_profile_detected():
    hw = diag.collect_hardware_profile()
    assert hw["cpu_logical_cores"] >= 4
    assert hw["ram_total_mib"] >= 8000
    if hw["gpu_name"] is not None:
        assert "RTX 3050" in hw["gpu_name"] or hw["gpu_vram_total_mib"] > 0
