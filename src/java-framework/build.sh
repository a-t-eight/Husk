#!/bin/bash
# build.sh <out.dex> -- Husk's Java framework (android.* for apps run on Husk's Dalvik runtime): javac, then d8.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
JDK="${HUSK_JDK:-${JAVA_HOME:-}}"
if [ -z "$JDK" ]; then JDK="$(/usr/libexec/java_home -v 17+)"; fi
D8="${HUSK_D8:?Run scripts/prepare_dependencies.py and source third_party/build/dependencies.env}"
for tool in java javac jar; do
    [ -x "$JDK/bin/$tool" ] || { echo "Set HUSK_JDK or JAVA_HOME to a full JDK 17 or newer" >&2; exit 1; }
done
[ -s "$D8" ] || { echo "R8 jar missing: $D8" >&2; exit 1; }
OUT="${1:-$HERE/../../build/husk-framework.dex}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python3 "$HERE/gen/gles20.py" "$HERE/src/android/opengl" "$HERE/../translation-layer-next/husk-tl-dvm-gles20.c" >/dev/null
# libcore's own classes that javac's JDK does not have (org.xmlpull): compiled to build against, not put in the dex
stubs=()
while IFS= read -r -d '' file; do stubs+=("$file"); done < <(find "$HERE/stubs" -name '*.java' -print0)
sources=()
while IFS= read -r -d '' file; do sources+=("$file"); done < <(find "$HERE/src" "$HERE/gen-src" -name '*.java' -print0)
"$JDK/bin/javac" -nowarn --release 8 -encoding UTF-8 -d "$TMP/stubs" "${stubs[@]}"
if ! "$JDK/bin/javac" -nowarn --release 8 -encoding UTF-8 -Xmaxerrs 100000 -cp "$TMP/stubs" -d "$TMP/classes" "${sources[@]}" 2> "$TMP/javac.log"; then
    grep -v "^Note:" "$TMP/javac.log" > "${JAVAC_LOG:-/dev/stderr}"; echo "javac failed" >&2; exit 1
fi
(cd "$TMP/classes" && "$JDK/bin/jar" cf "$TMP/classes.jar" .)
"$JDK/bin/java" -cp "$D8" com.android.tools.r8.D8 --min-api 26 --output "$TMP" "$TMP/classes.jar"
mkdir -p "$(dirname "$OUT")"
mv "$TMP/classes.dex" "$OUT"

# Core dex: handwritten src/ only (and nested types of those classes). Generated
# gen-src stubs stay out so AOSP framework.jar can define them when it is on the
# boot path. Nested stubs of a handwritten outer class stay here so Activity$Foo
# still matches Husk's Activity.
CORE="${OUT%.dex}-core.dex"
python3 - "$HERE/src" "$TMP/classes" "$TMP/core-classes" <<'PY'
import os, shutil, sys
src, classes, dest = sys.argv[1:4]
os.makedirs(dest, exist_ok=True)
kept = 0
for dirpath, _, files in os.walk(classes):
    for name in files:
        if not name.endswith(".class"):
            continue
        rel = os.path.relpath(os.path.join(dirpath, name), classes)
        outer = rel[:-6].split("$", 1)[0]
        if os.path.isfile(os.path.join(src, outer + ".java")):
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(dirpath, name), out)
            kept += 1
print(kept)
PY
(cd "$TMP/core-classes" && "$JDK/bin/jar" cf "$TMP/core.jar" .)
mkdir -p "$TMP/core-out"
"$JDK/bin/java" -cp "$D8" com.android.tools.r8.D8 --min-api 26 --output "$TMP/core-out" "$TMP/core.jar"
mv "$TMP/core-out/classes.dex" "$CORE"
rm -rf "$TMP"
echo "built $OUT"
echo "built $CORE"
