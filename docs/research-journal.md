# Engineering Research Journal — inFAMOUS: Second Son on `shadPS4`

## Entry 1 — Hardware & Platform Baseline Audit
- **Date**: 2026-10-03
- **Objective**: Establish exact hardware capabilities, driver versions, memory limits, and thermal/power constraints on the target Windows 11 laptop before modifying `shadPS4`.
- **Measured Telemetry**:
  - **CPU**: 12th Gen Intel Core i5-12450H (4 Performance cores + 4 Efficiency cores, 12 logical threads, 2.0 GHz base / 4.4 GHz boost).
  - **dGPU (`gpu_id = 0`)**: NVIDIA GeForce RTX 3050 Laptop GPU (`0x25a2`), `4096 MiB` GDDR6 dedicated VRAM, `4096 MiB` BAR1 aperture, `60.00 W` default TGP (`75.00 W` max limit), NVIDIA driver `617.14` (`32.0.16.1714`), Vulkan `1.4.351`.
  - **iGPU (`gpu_id = 1`)**: Intel UHD Graphics (`0x46a3`), Vulkan `1.3.250`.
  - **System RAM**: `16,052 MiB` physical RAM.
  - **Existing `%APPDATA%\shadPS4` State**: Found legacy `config.toml` from August 2025 without `config.json`. In `shadPS4` `v0.19.x` (`src/core/emulator_settings.cpp:359`), if `config.toml` exists while `config.json` is absent, `EmulatorSettingsImpl::Load("")` invokes a blocking `SDL_ShowMessageBox` modal ("Config Migration"), halting any CLI/automated launch until dismissed.
- **Action Taken**: Provisioned `%APPDATA%\shadPS4\config.json` and per-serial custom configs (`CUSA00004.json`, `CUSA00223.json`, `CUSA00046.json`, `CUSA00359.json`, `CUSA00575.json`, `CUSA00897.json`) locking `gpu_id = 0` (RTX 3050 Laptop GPU) and enabling `pipeline_cache_enabled = true`.

---

## Entry 2 — Upstream Issue & PR Investigation for inFAMOUS: Second Son
- **Objective**: Cross-reference upstream `shadps4-emu/shadPS4` issues and pull requests for `CUSA00004` / `CUSA00223` against the current `main` branch (`8e23388af83fe31ec6372a29e3940e2b459dfc6d`).
- **Findings**:
  1. **Issue `#4387` & PR `#4802` (Crash after Sucker Punch Splash Screen on Windows)**:
     - On Windows x86_64 hosts, guest Orbis threads use the System V AMD64 ABI, which permits leaf functions to store up to 128 bytes below `RSP` (`[RSP - 128, RSP)`) without adjusting `RSP`.
     - Whenever Windows delivers an exception or interrupt on a guest thread (such as `PageManager` `STATUS_GUARD_PAGE_VIOLATION` / `STATUS_ACCESS_VIOLATION` used by `BufferCache` and `TextureCache`), the Windows kernel pushes context/exception records directly onto the thread's `RSP`, overwriting the guest's red zone.
     - PR `#4802` added `src/core/cpu_patches.cpp` (`PatchGuestCodeRedZone`), which wraps red-zone-touching instructions with `lea rsp, [rsp - 0x80]` / `lea rsp, [rsp + 0x80]` via a 2-byte `int 0x20` (`CD 20`) trampoline, but defaulted `redzone_patches` to `false` in `GeneralSettings` (`src/core/emulator_settings.h:207`).
  2. **Issue `#4790` (`ErrorOutOfDeviceMemory` Crash in `buffer.cpp` / `image.cpp` during Gameplay)**:
     - Reported on `CUSA00004` v01.07: `Critical buffer.cpp:99 operator(): Assertion Failed! Failed allocating buffer with error ErrorOutOfDeviceMemory`.
     - Inspecting `src/video_core/texture_cache/texture_cache.cpp` revealed a critical logic inversion in `TextureCache::GarbageCollectImages()` (line 931) and `TextureCache::GarbageCollectSamplers()` (line 992):
       ```cpp
       ticks_to_destroy = aggresive ? 160 : pressured ? 80 : 16;
       image_lru_cache.ForEachItemBelow(gc_tick - ticks_to_destroy, clean_up);
       ```
       Because `ForEachItemBelow(threshold_tick)` only visits LRU items with `last_used_tick <= gc_tick - ticks_to_destroy`, a **larger** `ticks_to_destroy` means **fewer** items qualify for eviction!
       - Under normal load (`aggresive == false, pressured == false`), `ticks_to_destroy = 16`, so any texture unused for just 16 ticks was evicted—even more so because `trigger_gc_memory = (device_local_memory - mem_threshold) / 2` evaluated to `0` on any GPU with `<= 8 GB` VRAM (`TARGET_GC_THRESHOLD = 8_GB`), causing GC to run on every single frame from 0 MiB VRAM usage!
       - Under critical VRAM pressure (`aggresive == true`), `ticks_to_destroy` jumped to `160`, meaning the garbage collector **refused to evict any texture used within the last 160 ticks**! Combined with `VMA_ALLOCATION_CREATE_WITHIN_BUDGET_BIT` in `UniqueBuffer::Create` and `UniqueImage::Create`, 4 GB and 8 GB GPUs inevitably hit `VK_ERROR_OUT_OF_DEVICE_MEMORY` and crashed.
  3. **Issues `#4790`, `#4814`, `#4869` (`Bc3UnormBlock` / `Bc5UnormBlock` `eErrorFormatNotSupported`)**:
     - `ImageUsageFlags` (`src/video_core/texture_cache/image.cpp:45`) unconditionally sets `vk::ImageUsageFlagBits::eStorage` on compressed block textures (`info.props.is_block`).
     - When `getImageFormatProperties2` returns `vk::Result::eErrorFormatNotSupported`, `Image::Image` logged an error (`image format Bc3UnormBlock type e2D is not supported`) without stripping `eStorage` from `usage_flags` before calling `vmaCreateImage`.
  4. **Issues `#4814`, `#4869` (Missing Global Lighting & Missing Smoke/Neon Particles)**:
     - Sucker Punch's engine dispatches compute shaders for tile/cluster light culling, ambient occlusion/exposure, and particle simulation, and reads back small buffer headers on the CPU.
     - With `readbacks_mode = Disabled (0)`, these buffers remain zeroed on the CPU, resulting in black ambient lighting and missing smoke/neon particles.
     - With `readbacks_mode = Precise (2)` or `Relaxed (1)`, `BufferCache::ReadMemory` (`src/video_core/buffer_cache/buffer_cache.cpp:112`) expanded every readback page fault to a `512 KB` window (`constexpr u64 WindowSize = 512_KB`), stalling the GPU command queue on large regions. Bounding this window to `64 KB` when `InfamousSecondSonProfile` is active preserves compute lighting and particle readbacks while cutting worst-case synchronous scan range by `8x`.
  5. **Issue `#3274` & PR `#4742` (Asset Streaming Softlock / `EINVAL` on Guarded Guest Memory)**:
     - On Windows, `std::fread` directly into guest pages watched by `PageManager` (`PAGE_NOACCESS` / `PAGE_READONLY`) can fail with `errno = EINVAL` inside the CRT/kernel `ReadFile` path. Adding a host staging buffer fallback in `Common::FS::IOFile::ReadRaw` (`src/common/io_file.h:171`) ensures the file read succeeds into host memory and triggers the user-mode VEH watcher via `std::memcpy`.
  6. **Issues `#3728`, `#3274` (Controller Input Lockups & Stencil Graffiti Motion Requirement)**:
     - `GameController::ReadStates` (`src/input/controller.cpp:86`) returned `0` when `states_num > 1` and `m_states_queue` was empty, leaving `pData[0]` uninitialized in games that poll `scePadRead` with `count > 1`.
     - Furthermore, inFAMOUS: Second Son requires shaking and tilting the DualShock 4 during stencil spray-painting segments. Synthesizing accelerometer/gyroscope oscillations when `L2`/`R2` are held on non-gyro controllers/keyboards allows progression without physical motion hardware.
