# Build, Test & Execution Guide (`Windows 11 x64`)

## 1. Isolated Python Virtual Environment (`.venv`)

This workspace includes an isolated Python 3.13 virtual environment at `.venv/` equipped with:
- `cmake` (`4.4.3`)
- `ninja` (`1.13.2`)
- `pytest` (`9.1.1`)
- `psutil` (`7.2.2`)
- `pyyaml` (`6.0.3`)

### Running the Automated Python Test & Diagnostic Suite

```powershell
# Run all regression tests (VRAM GC math, param.sfo parser, log analyzer, config & C++ patch checks)
.\.venv\Scripts\pytest.exe tests/python/test_second_son_suite.py -v

# Run live hardware audit, VRAM threshold comparison, and game dump discovery
.\.venv\Scripts\python.exe tools/second_son_diagnostics.py --check-all

# Analyze a shadPS4 log file for inFAMOUS: Second Son issues
.\.venv\Scripts\python.exe tools/second_son_diagnostics.py --analyze-log "$env:APPDATA\shadPS4\log\CUSA00004.log"
```

---

## 2. Building `shadPS4` on Windows 11 (`clang-cl` + `Ninja`)

### Prerequisites
- Visual Studio 2022/2026 **Desktop development with C++** workload including:
  - MSVC v143/v144 C++ x64/x86 build tools
  - Windows 10/11 SDK
  - C++ Clang Compiler for Windows (`clang-cl`)
- Git with submodules initialized (`git submodule update --init --recursive`)

### Configure, Build, and Run C++ Unit Tests

```powershell
# Initialize submodules if not already fetched
git submodule update --init --recursive

# Configure Release build using Ninja and clang-cl
.\.venv\Scripts\cmake.exe -B build -G Ninja `
  -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_C_COMPILER=clang-cl `
  -DCMAKE_CXX_COMPILER=clang-cl `
  -DBUILD_TESTING=ON `
  -DENABLE_QT_GUI=OFF

# Build shadPS4 and unit test targets
.\.venv\Scripts\cmake.exe --build build --parallel

# Run CTest unit test suite (including EmulatorSettingsTest.InfamousSecondSonCompatibilityBootProfile)
.\.venv\Scripts\ctest.exe --test-dir build --output-on-failure
```

### GitHub Actions Automated Build
Every push to `infamous-second-son-compatibility` on `suryastark6/infamous-second-son-shadps4` triggers the `.github/workflows/build.yml` workflow, which builds both SDL and Qt Windows x64 binaries with `clang-cl` and runs `ctest` on `windows-2025-vs2026`.

---

## 3. Placing Your Legitimate Game Dump & Launching

1. Place your legally dumped, decrypted **inFAMOUS: Second Son** folder (`CUSA00004`, `CUSA00223`, `CUSA00046`, or `CUSA00359`) at:
   - `C:\games\ps4\CUSA00004\` (already registered in `%APPDATA%\shadPS4\config.json`), **or**
   - Any directory of your choice.
2. Verify that the game dump contains:
   - `eboot.bin`
   - `sce_sys/param.sfo`
   - `sce_module/libc.prx` and `sce_module/libSceFios2.prx`
3. Run the diagnostic check to confirm detection:
   ```powershell
   .\.venv\Scripts\python.exe tools/second_son_diagnostics.py --check-all
   ```
4. Launch via CLI:
   ```powershell
   .\build\shadPS4.exe -g "C:\games\ps4\CUSA00004\eboot.bin"
   ```
   Or launch by serial once indexed in `C:\games\ps4`:
   ```powershell
   .\build\shadPS4.exe -g CUSA00004
   ```
