# Preparing build dependencies

`scripts/ci_build.sh` now prepares the inputs that used to come from the upstream
developer's local directories. It stages Android fonts and CA certificates, builds
Husk's Java framework with R8, and builds the PS4 module before packaging the app.
No Apple signing certificate is required.

## Build on GitHub

Open **Actions → Build Husk iOS → Run workflow**. Use the workflow from `main`,
then enter the branch, tag or commit to build. The default is
`bugfix/p12-macverification`, which includes both certificate-import fixes.

The workflow uses GitHub's standard Apple Silicon `xcode-27` runner, installs the
build tools, runs the available preparation/MAC tests, and builds the packages.
No Apple certificate, private key or signing-account secret is needed. A successful
run uploads `Husk.ipa`, `Husk.tipa`, SHA-256 checksums and the source commit in the
**Artifacts** section. Artifacts expire after 14 days. Build logs are uploaded on
success or failure. Sign the IPA with your sideloading tool when installing it.

The workflow is manual; pushing a commit does not start a build. It does not publish
a release or install anything on a device. The first build uses a clean dependency
checkout; no native build-output cache is restored.

## Host tools

Use a Mac with full Xcode selected, Python 3.11+, and a full JDK 17 or newer:

```sh
brew install meson ninja pkg-config python xcodegen qemu e2fsprogs cmake ccache openjdk@17
export JAVA_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"
export DEBUGFS="$(brew --prefix e2fsprogs)/sbin/debugfs"
rustup target add aarch64-apple-ios
python3 -m venv build/python
source build/python/bin/activate
python -m pip install -r scripts/requirements-build.txt
```

On a runner with a JDK already installed, use its `JAVA_HOME`; `HUSK_JDK` takes
precedence. Rust/rustup must be installed before adding the iOS target. Homebrew's
e2fsprogs is keg-only; the preparation script also discovers it via `brew --prefix`.
Keep the Python environment active during the build: virglrenderer's Gallium
format generator requires PyYAML. The GitHub workflow sets this up automatically.

## Preparation and builds

```sh
# Download sources/R8 and extract Android resource inputs; does not compile Husk.
python3 scripts/prepare_dependencies.py

# Or inspect/fetch the PS4/FEX sources and R8 without downloading the Android image.
python3 scripts/prepare_dependencies.py --sources-only

# Full build (also runs preparation):
./scripts/ci_build.sh
```

Downloads are cached in `third_party/sources/`; source checkouts and extracted
inputs live in `third_party/build/`. Both are ignored by Git. The Android download
is about 2.06 GB, with additional temporary space needed for the raw disk and
system filesystem. Allow tens of GB of free disk space for the complete native
build. Temporary Android extraction files are removed on success or failure.

The script writes `third_party/build/dependencies.env` for standalone steps:

```sh
source third_party/build/dependencies.env
./scripts/build_android_system.sh --prepared "$HUSK_ANDROID_INPUTS"
HUSK_AOSP=0 ./scripts/build_java_runtime.sh "$HUSK_ANDROID_INPUTS/apex"
./scripts/build_ps4_ios.sh
```

Run full preparation before the Android steps; `--sources-only` deliberately leaves
the Android inputs unprepared. The PS4 build compiles its native ImGui font-embedding
helper before configuring the iOS cross-build.
It temporarily patches ImGuiFileDialog to create directories with C++ filesystem
calls instead of invoking a shell, which iOS disallows. The patch is reversed on
exit so the pinned dependency checkout remains clean.
Only hwinfo's CPU, RAM and OS components are built, matching the emulator's
call sites. Unused desktop hardware probes are disabled because their macOS
IOKit headers are unavailable in the iOS SDK. Existing PS4 build directories
are reconfigured so these options also apply to incremental builds.

## Sources and integrity

`scripts/dependencies.json` is the source of truth for these pins:

| Input | Source |
| --- | --- |
| PS4 module | [AetherPS4, husk branch](https://github.com/Leviidev/AetherPS4/tree/03c2deba16f6450f651a51b0e6ae45b66305022b), commit `03c2deba16f6450f651a51b0e6ae45b66305022b` |
| Hosted-JIT FEXCore | [Fathom, husk-fex branch](https://github.com/Leviidev/Fathom/tree/d6195b0baa392ee53de97e49e5ae5123108f77d3), commit `d6195b0baa392ee53de97e49e5ae5123108f77d3` |
| Android runtime inputs | [Husk's Lineage v12 disk image](https://github.com/Leviidev/Husk/releases/tag/lineage-v2), SHA-256 `a8dbaecc8fdcd682991b078d6bbb7df6972e459208634fa8dcbad24f17208d6a` |
| Java bytecode tool | [Google R8](https://r8.googlesource.com/r8/), version 8.5.35, SHA-256 `cb112407ef132dfcb1e5e1a9d4e6fefa89807822e65a9ffbdbb68ee442985405` |

Git dependencies use detached, pinned commits. Required PS4 submodules use their
recorded commits; unrelated desktop/Android dependencies are skipped. Preparation
checks that the console ABI matches Husk and that FEX provides the hosted-JIT API.
Existing source checkouts with a different revision or tracked modifications are
rejected, not reset. Downloaded files, including cached downloads, are verified
against SHA-256 before use. Extracted inputs are checked for required files before
being published as a complete cache.

These pins establish available build inputs; they do not assert that these are the
exact dependency revisions used to build the upstream 3.3 IPA. Other pre-existing
fetchers (including WebKit/ANGLE) retain their current revision-selection behavior.

The default build omits the optional AOSP framework bundle, matching its absence
in the published 3.3 IPA. For an explicit AOSP build, invoke `build_java_runtime.sh`
with `HUSK_AOSP=1` and `ANDROID_SYSROOT` pointing at a prepared Android system tree.

## Validation

```sh
python3 -B -m unittest discover -s tests -p test_prepare_dependencies.py -v
```

These offline tests cover logical-partition extraction, malformed/truncated input,
APEX/CAPEX decoding, download integrity and cache failures. A successful preparation
is not proof of a successful full app build or runtime compatibility. Validate the
complete IPA on the target runner and device before distributing it.
