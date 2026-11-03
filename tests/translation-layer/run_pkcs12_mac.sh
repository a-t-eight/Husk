#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# macOS + OpenSSL CLI; all generated keys, fixtures and binaries are disposable.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
OUT=$(mktemp -d "${TMPDIR:-/tmp}/husk-pkcs12-mac.XXXXXX")
trap 'rm -rf "$OUT"' EXIT HUP INT TERM
umask 077
xcrun clang -O1 -g -fsanitize=address,undefined -Wno-deprecated-declarations \
    "$ROOT/tests/translation-layer/pkcs12_mac.c" \
    -framework Security -framework CoreFoundation -o "$OUT/pkcs12_mac"
python3 "$ROOT/tests/translation-layer/test_pkcs12_mac.py" "$OUT/pkcs12_mac" "$OUT"
