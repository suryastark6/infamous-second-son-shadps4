// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <string>
#include <string_view>
#include "common/types.h"

namespace Core {

struct InfamousSecondSonProfile {
    bool active{false};
    bool is_first_light{false};
    bool redzone_workaround_applied{false};
    bool compute_readback_workaround_applied{false};
    bool compressed_block_storage_workaround_active{false};
    bool low_vram_spillover_active{false};
    bool guarded_guest_io_workaround_active{false};
    bool motion_shake_fallback_active{false};
    u64 adaptive_readback_max_bytes{64ULL * 1024ULL};
};

class GameCompatibilityManager {
public:
    static constexpr std::array<std::string_view, 4> kSecondSonSerials = {
        "CUSA00004", // Europe / Australia
        "CUSA00223", // North America
        "CUSA00046", // Asia
        "CUSA00359", // Japan
    };

    static constexpr std::array<std::string_view, 2> kFirstLightSerials = {
        "CUSA00575", // North America
        "CUSA00897", // Europe
    };

    static GameCompatibilityManager& Instance();

    static bool IsInfamousSecondSonSerial(std::string_view serial);
    static bool IsInfamousFirstLightSerial(std::string_view serial);
    static bool IsInfamousSuckerPunchTitle(std::string_view serial, std::string_view title = "");

    void OnGameBoot(std::string_view serial, std::string_view title, std::string_view app_version);
    void Reset();

    bool IsSecondSonActive() const noexcept {
        return m_second_son_active.load(std::memory_order_relaxed);
    }

    const std::string& GetActiveSerial() const noexcept {
        return m_active_serial;
    }

    const std::string& GetActiveTitle() const noexcept {
        return m_active_title;
    }

    const InfamousSecondSonProfile& GetSecondSonProfile() const noexcept {
        return m_profile;
    }

    u64 GetEffectiveReadbackMaxRangeBytes(u64 default_max_bytes) const noexcept;
    bool ShouldSynthesizeSprayCanMotion() const noexcept;

private:
    GameCompatibilityManager() = default;

    std::atomic<bool> m_second_son_active{false};
    std::string m_active_serial;
    std::string m_active_title;
    std::string m_active_version;
    InfamousSecondSonProfile m_profile{};
};

} // namespace Core
