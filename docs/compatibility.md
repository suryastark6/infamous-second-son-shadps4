# Compatibility Matrix & Root-Cause Analysis — inFAMOUS: Second Son (`shadPS4`)

## 1. Supported Title Serials & Editions

| Title | Region | Title ID (`SERIAL`) | Tested Patch Version | Engine | Compatibility Profile |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **inFAMOUS: Second Son** | Europe / Australia | `CUSA00004` | `01.07` | Sucker Punch Engine (Orbis SDK `01.60` / `01.70`) | `InfamousSecondSonProfile` (Auto-Enabled) |
| **inFAMOUS: Second Son** | North America | `CUSA00223` | `01.07` | Sucker Punch Engine (Orbis SDK `01.60` / `01.70`) | `InfamousSecondSonProfile` (Auto-Enabled) |
| **inFAMOUS: Second Son** | Asia | `CUSA00046` | `01.07` | Sucker Punch Engine (Orbis SDK `01.60` / `01.70`) | `InfamousSecondSonProfile` (Auto-Enabled) |
| **inFAMOUS: Second Son** | Japan | `CUSA00359` | `01.07` | Sucker Punch Engine (Orbis SDK `01.60` / `01.70`) | `InfamousSecondSonProfile` (Auto-Enabled) |
| **inFAMOUS: First Light** | North America | `CUSA00575` | `01.04` | Sucker Punch Engine (Orbis SDK `01.75`) | `InfamousSecondSonProfile` (Auto-Enabled) |
| **inFAMOUS: First Light** | Europe | `CUSA00897` | `01.04` | Sucker Punch Engine (Orbis SDK `01.75`) | `InfamousSecondSonProfile` (Auto-Enabled) |

---

## 2. Target Hardware & Host Baseline

| Component | Measured Specification |
| :--- | :--- |
| **System Model** | HP Victus Gaming Laptop 15-fa0xxx |
| **Operating System** | Microsoft Windows 11 Home Single Language (`10.0.26300`, Build `26300`) |
| **CPU** | 12th Gen Intel Core i5-12450H (8 physical cores [4P+4E], 12 threads, AVX2/BMI2/FMA) |
| **Primary dGPU (`gpu_id = 0`)** | NVIDIA GeForce RTX 3050 Laptop GPU (`deviceID = 0x25a2`, Ampere GA107M) |
| **Dedicated VRAM** | `4096 MiB` GDDR6 (`3258 MiB` initial WDDM budget, `4096 MiB` BAR1 aperture) |
| **GPU Power Limits** | `60.00 W` Default TGP (`75.00 W` Max Power Limit) |
| **GPU Driver & Vulkan** | NVIDIA `617.14` (`32.0.16.1714`), Vulkan API `1.4.351` (Instance `1.4.341`) |
| **Secondary iGPU (`gpu_id = 1`)** | Intel UHD Graphics (`deviceID = 0x46a3`, Vulkan `1.3.250`) |
| **System RAM** | `16 GB` (`16,052 MiB` usable) |
| **Display Panel** | `1920x1080` @ `143 Hz` |

---

## 3. Subsystem Compatibility Breakdown

| Subsystem | Upstream `8e23388` Status | Root Cause | Engineered Fix | Status After Fix |
| :--- | :--- | :--- | :--- | :--- |
| **Boot & Sucker Punch Splash (`CUSA00004` / `CUSA00223`)** | Crashes with `EXCEPTION_ACCESS_VIOLATION` (`0xC0000005`) after splash on Windows x86_64 | Sucker Punch's Orbis code uses the 128-byte System V AMD64 red zone (`[RSP - 0x80 .. RSP)`). Windows kernel/VEH page-fault delivery pushes exception frames onto the active thread stack (`RSP`), clobbering guest red-zone locals unless `redzone_patches` is active. | `GameCompatibilityManager::OnGameBoot` auto-enables `redzone_patches` (`StaticPatching` via `PatchGuestCodeRedZone` in `src/core/cpu_patches.cpp`) when booting any inFAMOUS serial on Windows x86_64. | **Fixed** |
| **4 GB VRAM Budget & Texture Eviction (`TextureCache`)** | Crashes with `Assertion Failed! Failed allocating buffer/image with error ErrorOutOfDeviceMemory` during gameplay | 1) `ticks_to_destroy = aggresive ? 160 : pressured ? 80 : 16` in `GarbageCollectImages` (`src/video_core/texture_cache/texture_cache.cpp:931`) is **inverted** relative to `ForEachItemBelow(gc_tick - ticks_to_destroy)`, refusing to evict textures younger than 160 ticks under critical VRAM pressure while thrashing 16-tick textures normally.<br>2) `trigger_gc_memory` evaluates to `0` on `<= 8 GB` GPUs.<br>3) `VMA_ALLOCATION_CREATE_WITHIN_BUDGET_BIT` hard-fails at the ~3.2 GB WDDM cap. | 1) Corrected `ticks_to_destroy = aggresive ? 4 : pressured ? 32 : 160` and raised batch eviction ceiling (`128` under critical pressure).<br>2) Set non-zero `trigger_gc_memory` floor (`~977 MiB` on 4 GB GPUs).<br>3) Added unbudgeted VMA spillover retry in `UniqueImage::Create`, `UniqueBuffer::Create`, and host-visible sparse fallback in `BufferCache::EnsureResident`. | **Fixed** |
| **BC1–BC7 Compressed Textures (`Image::Image`)** | Spam of `Render.Vulkan <Error> image.cpp:167: image format Bc3UnormBlock / Bc5UnormBlock is not supported` | `ImageUsageFlags` unconditionally attaches `vk::ImageUsageFlagBits::eStorage` to all block-compressed (`info.props.is_block`) images. NVIDIA/AMD/Intel Vulkan drivers reject `eStorage` on `Bc3UnormBlock` and `Bc5UnormBlock`. | When `getImageFormatProperties2` returns `eErrorFormatNotSupported` on a block-compressed image, `Image::Image` strips `eStorage` (and `eBlockTexelViewCompatible` / `eExtendedUsage` if required) and re-queries format properties before `vmaCreateImage`. | **Fixed** |
| **Global Illumination & Smoke/Neon Particles (`BufferCache`)** | Missing ambient/global lighting (pitch-black shadows) and invisible smoke/neon particles when `readbacks_mode = 0`; severe frame-time stalls when `readbacks_mode = 2` | Sucker Punch's deferred lighting & particle emitter passes read back small GPU compute summaries on the CPU. However, `BufferCache::ReadMemory` widens every readback fault to a `512 KB` window (`src/video_core/buffer_cache/buffer_cache.cpp:112`), causing large synchronous `scheduler.Finish()` stalls. | `GameCompatibilityManager::OnGameBoot` auto-enables `GpuReadbacksMode::Relaxed` (`1`) and narrows the readback coalescing window in `BufferCache::ReadMemory` from `512 KB` to `64 KB` when `adaptive_readbacks_enabled` is true. | **Fixed** |
| **Windows Guest Asset Streaming (`IOFile::ReadRaw`)** | Random streaming softlocks or `Assertion Failed! Failed to read file, error = Invalid argument` (`EINVAL`) | `std::fread` into guest memory pages protected with `PAGE_NOACCESS` / `PAGE_READONLY` by `PageManager` fails inside Windows `ReadFile` with `ERROR_INVALID_USER_BUFFER` / `ERROR_NOACCESS` (`errno = EINVAL`) because kernel I/O does not trigger user-mode VEH handlers cleanly on some buffer alignments. | Added a host staging buffer fallback in `IOFile::ReadRaw` (`src/common/io_file.h:171`) that clears `ferror`, seeks back to `start_offset`, reads into `std::vector<u8>`, and `std::memcpy`s into guest memory so user-mode VEH page watchers fire deterministically. | **Fixed** |
| **Input & Stencil Graffiti Motion Controls (`GameController`)** | 1) Camera/movement lockups when `scePadRead` is called with `count > 1` on an empty queue.<br>2) `NaN` quaternion orientation propagation.<br>3) Inability to complete spray-can graffiti on non-gyro gamepads/keyboard. | 1) `GameController::ReadStates` returned `0` without populating `states[0]` when `states_num > 1` and `m_states_queue` was empty.<br>2) `CalculateOrientation` divided by `norm` without checking `norm <= 1e-6f`.<br>3) Stencil graffiti requires Sixaxis/DS4 shake & tilt. | 1) `GameController::ReadStates` now writes `states[0] = m_state; return 1;` when the queue is empty.<br>2) Guarded `CalculateOrientation` against zero/non-finite norms.<br>3) Added automatic spray-can shake & tilt synthesis in `GameController::PushStateLocked` when `L2`/`R2` are held on non-gyro inputs. | **Fixed** |
