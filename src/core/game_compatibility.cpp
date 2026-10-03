// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "core/game_compatibility.h"

#include <algorithm>
#include <cctype>
#include "common/logging/log.h"
#include "core/emulator_settings.h"

namespace Core {

namespace {

bool ContainsCaseInsensitive(std::string_view haystack, std::string_view needle) {
    if (needle.empty() || haystack.size() < needle.size()) {
        return false;
    }
    auto it = std::search(haystack.begin(), haystack.end(), needle.begin(), needle.end(),
                          [](char a, char b) {
                              return std::tolower(static_cast<unsigned char>(a)) ==
                                     std::tolower(static_cast<unsigned char>(b));
                          });
    return it != haystack.end();
}

} // namespace

GameCompatibilityManager& GameCompatibilityManager::Instance() {
    static GameCompatibilityManager instance;
    return instance;
}

bool GameCompatibilityManager::IsInfamousSecondSonSerial(std::string_view serial) {
    return std::any_of(kSecondSonSerials.begin(), kSecondSonSerials.end(),
                       [serial](std::string_view candidate) { return serial == candidate; });
}

bool GameCompatibilityManager::IsInfamousFirstLightSerial(std::string_view serial) {
    return std::any_of(kFirstLightSerials.begin(), kFirstLightSerials.end(),
                       [serial](std::string_view candidate) { return serial == candidate; });
}

bool GameCompatibilityManager::IsInfamousSuckerPunchTitle(std::string_view serial,
                                                          std::string_view title) {
    if (IsInfamousSecondSonSerial(serial) || IsInfamousFirstLightSerial(serial)) {
        return true;
    }
    if (ContainsCaseInsensitive(title, "Second Son") ||
        ContainsCaseInsensitive(title, "First Light") ||
        ContainsCaseInsensitive(title, "inFAMOUS")) {
        return true;
    }
    return false;
}

void GameCompatibilityManager::Reset() {
    m_second_son_active.store(false, std::memory_order_relaxed);
    m_active_serial.clear();
    m_active_title.clear();
    m_active_version.clear();
    m_profile = InfamousSecondSonProfile{};
}

void GameCompatibilityManager::OnGameBoot(std::string_view serial, std::string_view title,
                                          std::string_view app_version) {
    Reset();
    m_active_serial = std::string(serial);
    m_active_title = std::string(title);
    m_active_version = std::string(app_version);

    auto settings = EmulatorSettingsImpl::GetInstance();
    if (!settings || !settings->IsSecondSonCompatEnabled()) {
        LOG_DEBUG(Compatibility,
                  "Game compatibility profile check skipped (disabled in settings) for {} ({})",
                  m_active_title, m_active_serial);
        return;
    }

    if (!IsInfamousSuckerPunchTitle(serial, title)) {
        return;
    }

    m_second_son_active.store(true, std::memory_order_relaxed);
    m_profile.active = true;
    m_profile.is_first_light = IsInfamousFirstLightSerial(serial) ||
                               ContainsCaseInsensitive(title, "First Light");
    m_profile.compressed_block_storage_workaround_active =
        settings->IsCompressedStorageFallbackEnabled();
    m_profile.low_vram_spillover_active = settings->IsVramSpilloverEnabled();
    m_profile.guarded_guest_io_workaround_active = true;
    m_profile.motion_shake_fallback_active = settings->IsMotionShakeFallbackEnabled();
    m_profile.adaptive_readback_max_bytes =
        settings->IsAdaptiveReadbacksEnabled() ? (64ULL * 1024ULL) : (512ULL * 1024ULL);

#if defined(_WIN32) && defined(ARCH_X86_64)
    // Sucker Punch's engine heavily utilizes the 128-byte System V AMD64 ABI red zone below RSP.
    // On Windows x86_64 hosts, OS exceptions/interrupts (including PageManager VEH page faults)
    // clobber the stack below RSP unless static red-zone instruction patching is enabled.
    if (!settings->IsRedZonePatchingEnabled()) {
        settings->SetRedZonePatchingEnabled(true, true);
        m_profile.redzone_workaround_applied = true;
        LOG_INFO(Compatibility,
                 "[inFAMOUS Compatibility] Enabled Windows guest red-zone static instruction "
                 "patching for {} ({}) to prevent post-splash stack corruption",
                 m_active_title, m_active_serial);
    } else {
        m_profile.redzone_workaround_applied = true;
    }
#endif

    // Sucker Punch's deferred lighting & particle engine reads back small compute buffer summaries
    // on the CPU to drive ambient/global illumination exposure and smoke/neon emitter state.
    // Leaving readbacks disabled causes dark unlit interiors and missing smoke/neon particles.
    if (settings->GetReadbacksMode() == GpuReadbacksMode::Disabled) {
        settings->SetReadbacksMode(GpuReadbacksMode::Relaxed, true);
        m_profile.compute_readback_workaround_applied = true;
        LOG_INFO(Compatibility,
                 "[inFAMOUS Compatibility] Enabled Relaxed GPU buffer readbacks with {} KB "
                 "adaptive window for {} ({}) to restore global illumination and particle emitters",
                 m_profile.adaptive_readback_max_bytes / 1024ULL, m_active_title, m_active_serial);
    }

    LOG_INFO(Compatibility,
             "Activated inFAMOUS Sucker Punch Engine compatibility profile for '{}' [{} v{}]: "
             "redzone_patch={}, readbacks_mode={}, adaptive_readback_kb={}, "
             "bc_storage_fallback={}, vram_spillover={}, guarded_io_staging={}, motion_fallback={}",
             m_active_title, m_active_serial, m_active_version,
             m_profile.redzone_workaround_applied, settings->GetReadbacksMode(),
             m_profile.adaptive_readback_max_bytes / 1024ULL,
             m_profile.compressed_block_storage_workaround_active,
             m_profile.low_vram_spillover_active, m_profile.guarded_guest_io_workaround_active,
             m_profile.motion_shake_fallback_active);
}

u64 GameCompatibilityManager::GetEffectiveReadbackMaxRangeBytes(
    u64 default_max_bytes) const noexcept {
    auto settings = EmulatorSettingsImpl::GetInstance();
    if (settings && settings->IsAdaptiveReadbacksEnabled() && IsSecondSonActive()) {
        return m_profile.adaptive_readback_max_bytes;
    }
    return default_max_bytes;
}

bool GameCompatibilityManager::ShouldSynthesizeSprayCanMotion() const noexcept {
    if (!IsSecondSonActive()) {
        return false;
    }
    auto settings = EmulatorSettingsImpl::GetInstance();
    return settings ? settings->IsMotionShakeFallbackEnabled()
                    : m_profile.motion_shake_fallback_active;
}

} // namespace Core
