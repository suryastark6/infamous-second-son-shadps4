# inFAMOUS: Second Son Compatibility Layer Architecture

## 1. Overview

`Core::GameCompatibilityManager` ([`src/core/game_compatibility.h`](../src/core/game_compatibility.h), [`src/core/game_compatibility.cpp`](../src/core/game_compatibility.cpp)) provides a modular, non-intrusive compatibility and performance profile for Sucker Punch Productions' **inFAMOUS: Second Son** (`CUSA00004`, `CUSA00223`, `CUSA00046`, `CUSA00359`) and **inFAMOUS: First Light** (`CUSA00575`, `CUSA00897`).

All workarounds are:
- **Scoped by Title ID / Engine Detection**: Activated in `Emulator::Run()` ([`src/emulator.cpp`](../src/emulator.cpp)) immediately after `EmulatorSettings.Load(id)` and `Common::Log::Switch(...)`.
- **Configurable**: Controlled via `EmulatorSettings` (`second_son_compat_enabled`, `motion_shake_fallback_enabled`, `adaptive_readbacks_enabled`, `vram_spillover_enabled`, `compressed_storage_fallback_enabled`).
- **Observable**: Logged through dedicated `Common::Log::Class::Compatibility` and `Common::Log::Class::Performance` channels.

---

## 2. Workaround Sub-Components

### 2.1 Windows Guest Red-Zone Protection (`redzone_workaround_applied`)
- **Problem**: Sucker Punch's Orbis compiler emits leaf functions that store temporary values in the 128-byte System V AMD64 red zone (`[RSP - 0x80, RSP)`). On Windows x86_64 hosts, Vectored Exception Handling (VEH) page faults triggered by `PageManager` (`BufferCache` / `TextureCache` memory watches) push Windows exception frames onto `RSP`, corrupting the guest red zone and crashing right after the Sucker Punch splash screen (`#4387`, `#4802`).
- **Mechanism**: When `OnGameBoot` detects an inFAMOUS serial on Windows x86_64 and `second_son_compat_enabled` is true, it enables `SetRedZonePatchingEnabled(true, true)` (`StaticPatching` in `src/core/cpu_patches.cpp`), which patches guest functions touching `[RSP - disp]` to adjust `RSP` by `0x80` around red-zone accesses.

### 2.2 Adaptive Compute Buffer Readbacks (`compute_readback_workaround_applied`)
- **Problem**: Sucker Punch's deferred lighting and particle engine dispatches compute shaders and reads back small buffer summaries on the CPU to drive global illumination exposure and smoke/neon particle emitters (`#4869`, `#4814`). Leaving `readbacks_mode` at `Disabled (0)` causes dark unlit interiors and missing smoke/neon particles, whereas stock readbacks widen every CPU page fault to a `512 KB` download window (`src/video_core/buffer_cache/buffer_cache.cpp:112`).
- **Mechanism**:
  1. `OnGameBoot` automatically upgrades `readbacks_mode` from `Disabled (0)` to `Relaxed (1)` as a game-specific override.
  2. `BufferCache::ReadMemory` queries `GameCompatibilityManager::Instance().GetEffectiveReadbackMaxRangeBytes(512_KB)`, narrowing the readback coalescing window to `64 KB` when `adaptive_readbacks_enabled` is true.

### 2.3 Compressed Block Texture Storage Flag Sanitization (`compressed_block_storage_workaround_active`)
- **Problem**: `ImageUsageFlags` (`src/video_core/texture_cache/image.cpp:45`) attaches `vk::ImageUsageFlagBits::eStorage` to all block-compressed textures (`BC1`–`BC7`). Vulkan drivers reject `eStorage` on `Bc3UnormBlock` (1D/2D) and `Bc5UnormBlock` (2D), logging `image format ... is not supported` (`#4790`, `#4814`, `#4869`).
- **Mechanism**: In `Image::Image` (`src/video_core/texture_cache/image.cpp`), if `getImageFormatProperties2` returns `vk::Result::eErrorFormatNotSupported` on a block-compressed texture and `IsCompressedStorageFallbackEnabled()` is true, `eStorage` (and `eBlockTexelViewCompatible` / `eExtendedUsage` if needed) is stripped from the image creation flags and `getImageFormatProperties2` is re-queried prior to `vmaCreateImage`.

### 2.4 4 GB VRAM GC Correction & Unbudgeted VMA Spillover (`low_vram_spillover_active`)
- **Problem**:
  1. `TextureCache::GarbageCollectImages` and `GarbageCollectSamplers` (`src/video_core/texture_cache/texture_cache.cpp:931, 992`) inverted `ticks_to_destroy` (`aggresive ? 160 : pressured ? 80 : 16`), refusing to evict textures younger than 160 ticks under critical VRAM pressure while evicting 16-tick textures normally.
  2. `trigger_gc_memory` evaluated to `0` on `<= 8 GB` GPUs.
  3. `VMA_ALLOCATION_CREATE_WITHIN_BUDGET_BIT` in `UniqueImage::Create` and `UniqueBuffer::Create` caused fatal `ErrorOutOfDeviceMemory` assertions as soon as the `~3.2 GB` WDDM budget on the 4 GB RTX 3050 was reached (`#4790`).
- **Mechanism**:
  1. Corrected `ticks_to_destroy = aggresive ? 4 : pressured ? 32 : 160` and raised batch eviction limit (`128` under critical pressure).
  2. Set a non-zero `trigger_gc_memory` floor (`~977 MiB` on a 4 GB GPU).
  3. Added unbudgeted `VMA_MEMORY_USAGE_AUTO` retry in `UniqueImage::Create` and `UniqueBuffer::Create`, plus a host-visible sparse memory fallback (`fallback_arena_memory_type_index`) in `BufferCache::EnsureResident`.

### 2.5 Windows Guarded Guest Memory File I/O Staging (`guarded_guest_io_workaround_active`)
- **Problem**: `std::fread` directly into guest memory protected by `PageManager` (`PAGE_NOACCESS` / `PAGE_READONLY`) can fail on Windows with `errno = EINVAL` (`#3274`, `#4742`), causing asset streaming softlocks or fatal assertions.
- **Mechanism**: In `Common::FS::IOFile::ReadRaw` (`src/common/io_file.h:171`), if `std::fread` sets `ferror(file)`, the stream error is cleared, the file offset is restored to `start_offset`, the read is performed into a temporary `std::vector<u8>` host buffer, and `std::memcpy` copies the bytes into guest memory (cleanly triggering user-mode VEH page watchers).

### 2.6 Sixaxis / DualShock 4 Spray-Can Motion Synthesis (`motion_shake_fallback_active`)
- **Problem**:
  1. `GameController::ReadStates` returned `0` without writing `states[0]` when `states_num > 1` and `m_states_queue` was empty, leaving `pData[0]` uninitialized.
  2. `CalculateOrientation` could produce `NaN` quaternions when `norm == 0`.
  3. Stencil graffiti side missions in inFAMOUS: Second Son require tilting the DualShock 4 by 90 degrees and shaking the spray can (`#3728`, `#3274`), blocking players on keyboard/mouse or XInput controllers.
- **Mechanism**:
  1. `GameController::ReadStates` now writes `states[0] = m_state; return 1;` when `read_count == 0`.
  2. `CalculateOrientation` guards against non-finite inputs and near-zero norms.
  3. When `ShouldSynthesizeSprayCanMotion()` is true and `L2`/`R2` are held without physical gyro activity, `GameController::PushStateLocked` synthesizes sinusoidal accelerometer (`28 m/s^2`) and gyroscope (`6 rad/s`) oscillations to satisfy the spray-can shake & tilt detector.
