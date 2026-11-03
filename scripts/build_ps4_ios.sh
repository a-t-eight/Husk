#!/bin/bash
# Builds the PS4 layer for the phone: husk-fex's FEXCore and Bachata-S4's core (branch husk) as libhuskps4.dylib, copied to
# build/ios-arm64/lib for project.yml to embed.
#
#   scripts/build_ps4_ios.sh            # build with the trees at BACHATA_DIR and HUSK_FEX_DIR
#
# Run prepare_dependencies.py first. BACHATA_DIR and HUSK_FEX_DIR can override
# the prepared checkouts; both must match scripts/dependencies.json.
set -euo pipefail
HUSK="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${BACHATA_DIR:-}" ] || [ -z "${HUSK_FEX_DIR:-}" ]; then
    . "$HUSK/third_party/build/dependencies.env"
fi
BACHATA="$BACHATA_DIR"
FEX="$HUSK_FEX_DIR"
FEX_SRC="$FEX/runtime/sources/fexcore-darwin"
FEX_BUILD="$BACHATA/runtime/build/fexcore-huskfex-ios"
CORE_BUILD="$BACHATA/runtime/build/huskps4-ios"
OUT="$HUSK/build/ios-arm64/lib"
JOBS="${JOBS:-$(sysctl -n hw.ncpu)}"

python3 - "$HUSK/scripts/dependencies.json" "$BACHATA" "$FEX" <<'PY'
import json, subprocess, sys
lock = json.load(open(sys.argv[1]))
for key, path in zip(('ps4', 'fex'), sys.argv[2:]):
    actual = subprocess.check_output(['git', '-C', path, 'rev-parse', 'HEAD'], text=True).strip()
    if actual != lock[key]['commit']:
        sys.exit(f'{key}: unexpected revision {actual}; run scripts/prepare_dependencies.py')
PY
CORE_REV="$(git -C "$BACHATA" rev-parse --short HEAD)"; FEX_REV="$(git -C "$FEX" rev-parse --short HEAD)"
echo "PS4 layer: core $CORE_REV, FEXCore $FEX_REV"

# ImGui's cross-build needs a native font embedding tool before CMake configures.
FONT_SOURCE="$BACHATA/externals/dear_imgui/misc/fonts/binary_to_compressed_c.cpp"
FONT_TOOL="$BACHATA/runtime/build/host-tools/Dear_ImGui_FontEmbed"
if [ ! -x "$FONT_TOOL" ] || [ "$FONT_SOURCE" -nt "$FONT_TOOL" ]; then
    mkdir -p "$(dirname "$FONT_TOOL")"
    xcrun --sdk macosx clang++ -O2 "$FONT_SOURCE" -o "$FONT_TOOL"
fi

if [ ! -f "$FEX_BUILD/CMakeCache.txt" ]; then
    cmake -S "$FEX_SRC" -B "$FEX_BUILD" -G "Unix Makefiles" -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_SYSROOT=iphoneos \
        -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=17.4 -DCMAKE_SYSTEM_PROCESSOR=arm64 -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_FEXCORE_ONLY=ON -DBUILD_FEXCORE_SMOKE=OFF -DBUILD_FEXCONFIG=OFF -DBUILD_TESTING=OFF -DBUILD_THUNKS=OFF \
        -DENABLE_LTO=OFF -DENABLE_CCACHE=ON -DENABLE_ASSERTIONS=OFF -DENABLE_JEMALLOC_GLIBC_ALLOC=OFF -DENABLE_OFFLINE_TELEMETRY=OFF \
        -DENABLE_FEX_ALLOCATOR=OFF -DTUNE_ARCH=generic -DTUNE_CPU=none -DCMAKE_CXX_FLAGS=-DMETA_NO_STD_FORWARD_DECLARATIONS
fi
# FEX's rpmalloc maps 256 MiB spans per thread and page type: tens of GB of address space for a game with ~30 threads,
# more than iOS's large-mapping window has room for beside the PS4's own reservations. The system allocator instead.
if ! grep -q "^ENABLE_FEX_ALLOCATOR:BOOL=OFF" "$FEX_BUILD/CMakeCache.txt"; then
    cmake -DENABLE_FEX_ALLOCATOR=OFF "$FEX_BUILD"
    rm -f "$FEX_BUILD/External/rpmalloc/librpmalloc.a"
fi
make -C "$FEX_BUILD" -j"$JOBS" FEXCore Common CommonTools JemallocLibs

# This pinned ImGuiFileDialog revision shells out to mkdir, unavailable on iOS.
# Keep the source checkout clean for the next dependency preparation run.
IGFD_DIR="$BACHATA/externals/ImGuiFileDialog"
IGFD_PATCH="$HUSK/patches/imgui-file-dialog-ios.patch"
if ! git -C "$IGFD_DIR" apply --reverse --check "$IGFD_PATCH" 2>/dev/null; then
    git -C "$IGFD_DIR" apply --check "$IGFD_PATCH"
    git -C "$IGFD_DIR" apply "$IGFD_PATCH"
    trap 'git -C "$IGFD_DIR" apply --reverse "$IGFD_PATCH"' EXIT
fi

# Reconfigure existing builds too, including changes to FEX's library list.
# The emulator uses only CPU/RAM/OS information; the other hwinfo probes pull
# in desktop APIs (including IOKit power-source headers unavailable on iOS).
cmake -S "$BACHATA" -B "$CORE_BUILD" -G Ninja -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_SYSROOT=iphoneos -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=17.4 -DCMAKE_BUILD_TYPE=Release -DENABLE_FEX_GUEST_CPU=ON \
    -DFEXCORE_GUEST_CPU_BUILD_DIR="$FEX_BUILD" -DFEXCORE_GUEST_CPU_SOURCE_DIR="$FEX_SRC" \
    -DENABLE_SHADPS4_IOS_LIB=ON -DENABLE_HUSK_MODULE=ON -DENABLE_DISCORD_RPC=OFF -DENABLE_UPDATER=OFF \
    -DIMGUI_FONT_EMBED_EXECUTABLE="$FONT_TOOL" \
    -DHWINFO_BATTERY=OFF -DHWINFO_DISK=OFF -DHWINFO_GPU=OFF -DHWINFO_MAINBOARD=OFF -DHWINFO_NETWORK=OFF \
    -DALLOWS_ONESHOT_TIMERS_WITH_TIMEOUT_ZERO_EXITCODE=0
ninja -C "$CORE_BUILD" huskps4

DYLIB="$CORE_BUILD/libhuskps4.dylib"
exports="$(nm -gU "$DYLIB" | awk '{print $3}')"
[ "$exports" = "_husk_console_module_v1" ] || { echo "error: libhuskps4 exports more than husk_console_module_v1:" >&2; echo "$exports" >&2; exit 1; }
if nm -u "$DYLIB" | grep -q BreakGetJITMapping; then echo "error: libhuskps4 still references BreakpointJIT" >&2; exit 1; fi
mkdir -p "$OUT"
install_name_tool -id @rpath/libhuskps4.dylib "$DYLIB"
cp "$DYLIB" "$OUT/libhuskps4.dylib"
echo "core $CORE_REV (Bachata-S4 husk), FEXCore $FEX_REV (husk-fex)" > "$OUT/libhuskps4.revision"
echo "libhuskps4.dylib: $(du -h "$OUT/libhuskps4.dylib" | cut -f1) -> $OUT"
