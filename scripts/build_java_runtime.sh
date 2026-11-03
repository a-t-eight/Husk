#!/bin/bash
# build_java_runtime.sh [apex-root] -- the files Husk's Dalvik runtime needs, laid out as Android's APEXes so ANDROID_*_ROOT work:
# libcore's jars and native libraries (ART APEX), ICU (i18n APEX), time zones (tzdata APEX), conscrypt, Husk's Java framework, and
# the platform's resources (framework-res.apk, stripped).
# The APEX root comes from the Lineage system image (see build_android_system.sh); output: build/java-runtime.
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
APEX="${1:?usage: build_java_runtime.sh <prepared-apex-root>}"
HTTP_LEGACY="${HTTP_LEGACY:-$(dirname "$APEX")/framework/org.apache.http.legacy.jar}"
FRAMEWORK_RES="${FRAMEWORK_RES:-$(dirname "$APEX")/framework/framework-res.apk}"
test -s "$APEX/com.android.art/javalib/core-oj.jar"
test -s "$HTTP_LEGACY"
test -s "$FRAMEWORK_RES"
OUT="$R/build/java-runtime"
rm -rf "$OUT"; mkdir -p "$OUT"
copy() { mkdir -p "$OUT/$(dirname "$1")"; cp "$APEX/$1" "$OUT/$1"; }
for j in core-oj core-libart okhttp bouncycastle apache-xml; do copy com.android.art/javalib/$j.jar; done
for l in libandroidio libbase libc++ libexpat libjavacore libnativehelper libopenjdk; do copy com.android.art/lib64/$l.so; done
copy com.android.i18n/javalib/core-icu4j.jar
for l in libicu libicui18n libicuuc libicu_jni libandroidicu; do [ -f "$APEX/com.android.i18n/lib64/$l.so" ] && copy com.android.i18n/lib64/$l.so; done
mkdir -p "$OUT/com.android.i18n/etc"; cp -R "$APEX/com.android.i18n/etc/icu" "$OUT/com.android.i18n/etc/"
mkdir -p "$OUT/com.android.tzdata"; cp -R "$APEX/com.android.tzdata/etc" "$OUT/com.android.tzdata/"
copy com.android.conscrypt/javalib/conscrypt.jar
# conscrypt's natives (HTTPS for Java code): its JNI library and the BoringSSL it links
for l in libjavacrypto libcrypto libssl; do copy com.android.conscrypt/lib64/$l.so; done
"$R/src/java-framework/build.sh" "$OUT/husk-framework.dex"
# Apache HttpClient (org.apache.http.legacy), the platform's own, from /system/framework of the same image
cp "$HTTP_LEGACY" "$OUT/org.apache.http.legacy.jar"
# the platform's resources (themes, styles, layouts, drawables), from the same system image, without what an iPhone never picks
python3 "$R/tools/strip_framework_res.py" "$FRAMEWORK_RES" "$OUT/framework-res.apk"
# Optional AOSP framework.jar (not in git). When present, Java apps prefer it over husk-framework.dex.
if [ "${HUSK_AOSP:-0}" != "0" ]; then
    "$R/scripts/copy_aosp_bundle.sh" "${ANDROID_SYSROOT:?Set ANDROID_SYSROOT when enabling HUSK_AOSP}" "$OUT/aosp"
fi
du -sh "$OUT"
