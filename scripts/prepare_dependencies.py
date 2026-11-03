#!/usr/bin/env python3
"""Fetch pinned PS4 sources and prepare Android inputs without building Husk.

Outputs stay in ignored third_party/ directories. Requires git, curl, qemu-img
and e2fsprogs debugfs. --sources-only needs just git and curl.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "scripts/dependencies.json").read_text())


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def capture(*args):
    return subprocess.check_output([str(a) for a in args], text=True).strip()


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(spec, directory):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (spec["sha256"][:12] + "-" + spec["url"].rsplit("/", 1)[1])
    if target.exists():
        if sha256(target) != spec["sha256"]:
            raise ValueError(f"Checksum mismatch in cached download: {target}")
        return target
    with tempfile.TemporaryDirectory(dir=directory) as work:
        part = Path(work) / "download"
        run("curl", "--fail", "--location", "--retry", "3", "--output", part, spec["url"])
        if sha256(part) != spec["sha256"]:
            raise ValueError(f"Checksum mismatch downloading {spec['url']}")
        part.rename(target)
    return target


def checkout(spec, directory, sparse=None):
    """Never reset or replace an existing checkout, including a dirty one."""
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory.parent) as work:
            tree = Path(work) / "source"
            run("git", "init", "--quiet", tree)
            run("git", "-C", tree, "remote", "add", "origin", spec["url"])
            run("git", "-C", tree, "fetch", "--depth", "1", "origin", spec["commit"])
            if sparse:
                run("git", "-C", tree, "sparse-checkout", "set", sparse)
            run("git", "-C", tree, "checkout", "--detach", spec["commit"])
            tree.rename(directory)
    if capture("git", "-C", directory, "rev-parse", "HEAD") != spec["commit"]:
        raise ValueError(f"Unexpected revision in {directory}; use a separate checkout")
    run("git", "-C", directory, "diff", "--quiet", "HEAD", "--ignore-submodules=untracked")
    return directory


def read_at(stream, offset, length):
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        raise ValueError("Truncated Android disk image")
    return data


def extract_system(raw, output):
    """Read the pinned image's GPT and Android logical-partition linear extents.

    Reject unsupported layouts instead of silently producing a corrupt filesystem.
    Offsets are in 512-byte sectors in this image (as in build_android_system.sh).
    """
    with raw.open("rb") as image:
        size = raw.stat().st_size
        gpt = read_at(image, 512, 92)
        if gpt[:8] != b"EFI PART":
            raise ValueError("Expected a GPT Android disk image")
        table_lba, count, stride = struct.unpack_from("<QII", gpt, 72)
        if count > 4096 or not 128 <= stride <= 4096:
            raise ValueError("Invalid GPT partition table")
        super_bounds = None
        for i in range(count):
            entry = read_at(image, table_lba * 512 + i * stride, stride)
            name = entry[56:128].decode("utf-16-le").rstrip("\0")
            if name == "super":
                start, end = struct.unpack_from("<QQ", entry, 32)
                super_bounds = (start * 512, (end + 1) * 512)
                break
        if not super_bounds or not 0 <= super_bounds[0] < super_bounds[1] <= size:
            raise ValueError("Missing or invalid super partition")
        base, limit = super_bounds
        header = read_at(image, base + 12288, 128)
        magic, major, minor, hsize = struct.unpack_from("<IHHI", header)
        if magic != 0x414C5030 or major != 10 or not 128 <= hsize <= 4096:
            raise ValueError("Unsupported Android logical-partition metadata")
        tsize = struct.unpack_from("<I", header, 44)[0]
        if tsize > 16 * 1024 * 1024 or base + 12288 + hsize + tsize > limit:
            raise ValueError("Invalid logical-partition table size")
        tables = read_at(image, base + 12288 + hsize, tsize)
        po, pn, ps = struct.unpack_from("<III", header, 80)
        eo, en, es = struct.unpack_from("<III", header, 92)
        if ps < 52 or es < 24 or po + pn * ps > tsize or eo + en * es > tsize:
            raise ValueError("Invalid logical-partition descriptors")
        for i in range(pn):
            partition = tables[po + i * ps:po + (i + 1) * ps]
            name = partition[:36].split(b"\0", 1)[0]
            if name not in (b"system", b"system_a"):
                continue
            first, number = struct.unpack_from("<II", partition, 40)
            if not number or first + number > en:
                raise ValueError("Invalid system extent range")
            with output.open("wb") as dest:
                for j in range(first, first + number):
                    sectors, kind, sector, source = struct.unpack_from("<QIQI", tables, eo + j * es)
                    offset, length = base + sector * 512, sectors * 512
                    if kind != 0 or source != 0 or not base <= offset <= offset + length <= limit:
                        raise ValueError("Unsupported or out-of-bounds system extent")
                    while length:
                        chunk = read_at(image, offset, min(length, 8 * 1024 * 1024))
                        dest.write(chunk)
                        offset += len(chunk)
                        length -= len(chunk)
            return
        raise ValueError("No system logical partition")


def debugfs(tool, image, command, expected):
    # debugfs can exit zero after a failed command: also require its output.
    run(tool, "-R", command, image)
    if not expected.exists() or (expected.is_file() and not expected.stat().st_size):
        raise ValueError(f"debugfs did not produce {expected}")


def extract_apex(archive, output):
    with zipfile.ZipFile(archive) as outer:
        if "original_apex" in outer.namelist():
            with outer.open("original_apex") as original, zipfile.ZipFile(original) as apex:
                with apex.open("apex_payload.img") as src, output.open("wb") as dest:
                    shutil.copyfileobj(src, dest)
        else:
            with outer.open("apex_payload.img") as src, output.open("wb") as dest:
                shutil.copyfileobj(src, dest)


def validate_android(dest):
    required = ["etc/fonts.xml", "etc/font_fallback.xml", "framework/org.apache.http.legacy.jar",
                "framework/framework-res.apk", "apex/com.android.i18n/javalib/core-icu4j.jar",
                "apex/com.android.conscrypt/javalib/conscrypt.jar"]
    required += [f"apex/com.android.art/javalib/{name}.jar" for name in
                 ("core-oj", "core-libart", "okhttp", "bouncycastle", "apache-xml")]
    required += [f"apex/com.android.art/lib64/{name}.so" for name in
                 ("libandroidio", "libbase", "libc++", "libexpat", "libjavacore", "libnativehelper", "libopenjdk")]
    required += [f"apex/com.android.conscrypt/lib64/{name}.so" for name in ("libjavacrypto", "libcrypto", "libssl")]
    for name in required:
        if not (dest / name).is_file() or not (dest / name).stat().st_size:
            raise ValueError(f"Missing Android runtime input: {dest / name}")
    for name in ("fonts", "apex/com.android.conscrypt/cacerts", "apex/com.android.i18n/etc/icu", "apex/com.android.tzdata/etc"):
        if not any(p.is_file() and p.stat().st_size for p in (dest / name).rglob("*")):
            raise ValueError(f"Empty Android resource directory: {dest / name}")


def android_inputs(image, dest, tool):
    if dest.exists():
        if (dest / ".complete").read_text().strip() != LOCK["android"]["sha256"]:
            raise ValueError(f"Unexpected Android inputs: {dest}")
        validate_android(dest)
        return
    with tempfile.TemporaryDirectory(dir=dest.parent) as directory:
        work = Path(directory)
        raw, system = work / "vda.raw", work / "system.img"
        run("qemu-img", "convert", "-f", "qcow2", "-O", "raw", image, raw)
        extract_system(raw, system)
        raw.unlink()
        staged = work / "inputs"
        staged.mkdir()
        # debugfs uses its own command parser. Quote local output paths explicitly.
        def dump(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            debugfs(tool, system, f'dump {source} "{target}"', target)
        debugfs(tool, system, f'rdump /system/fonts "{staged}"', staged / "fonts")
        for name in ("fonts.xml", "font_fallback.xml"):
            dump("/system/etc/" + name, staged / "etc" / name)
        for name in ("org.apache.http.legacy.jar", "framework-res.apk"):
            dump("/system/framework/" + name, staged / "framework" / name)
        for module in ("com.android.art", "com.android.i18n", "com.android.tzdata", "com.android.conscrypt"):
            # Lineage uses compressed APEXes; support uncompressed APEXes too.
            archive = work / (module + ".apex")
            for suffix in ("capex", "apex"):
                run(tool, "-R", f'dump /system/apex/{module}.{suffix} "{archive}"', system)
                if archive.exists() and archive.stat().st_size:
                    break
            else:
                raise ValueError(f"Missing APEX: {module}")
            payload = work / (module + ".img")
            extract_apex(archive, payload)
            target = staged / "apex" / module
            target.mkdir(parents=True)
            debugfs(tool, payload, f'rdump / "{target}"', target / "apex_manifest.pb")
            payload.unlink()
            archive.unlink()
        validate_android(staged)
        (staged / ".complete").write_text(LOCK["android"]["sha256"] + "\n")
        staged.rename(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources-only", action="store_true", help="fetch PS4/FEX/R8, without the Android disk image")
    args = parser.parse_args()
    source = ROOT / "third_party/build"
    downloads = ROOT / "third_party/sources"
    source.mkdir(parents=True, exist_ok=True)
    ps4 = checkout(LOCK["ps4"], source / ("AetherPS4-" + LOCK["ps4"]["commit"][:12]))
    # Skip unrelated desktop/Android dependencies (not used by the pinned iOS CMake path).
    excluded = {"externals/mesa-kosmickrisp", "externals/vulkan-loader", "externals/winlator-app",
                "runtime/sources/box64", "externals/ext-wepoll", "externals/libusb", "externals/discord-rpc"}
    modules = capture("git", "-C", ps4, "config", "--file", ".gitmodules", "--get-regexp", r"submodule\..*\.path")
    paths = [line.split(None, 1)[1] for line in modules.splitlines() if line.split(None, 1)[1] not in excluded]
    run("git", "-C", ps4, "submodule", "update", "--init", "--recursive", "--depth", "1", "--jobs", "4", "--", *paths)
    fex = checkout(LOCK["fex"], source / ("husk-fex-" + LOCK["fex"]["commit"][:12]), "runtime/sources/fexcore-darwin")
    header = fex / "runtime/sources/fexcore-darwin/FEXCore/include/FEXCore/Utils/AllocatorHooks.h"
    if "SetJITHostHooks" not in header.read_text():
        raise ValueError("FEX source lacks Husk's hosted-JIT interface")
    if (ps4 / "src/platform/husk/husk_console_abi.h").read_bytes() != (ROOT / "src/translation-layer-next/husk-console-abi.h").read_bytes():
        raise ValueError("PS4 module ABI differs from Husk")
    r8 = download(LOCK["r8"], downloads)
    android = source / ("android-" + LOCK["android"]["sha256"][:12])
    if not args.sources_only:
        tool = os.environ.get("DEBUGFS") or shutil.which("debugfs")
        if not tool and shutil.which("brew"):
            tool = capture("brew", "--prefix", "e2fsprogs") + "/sbin/debugfs"
        if not tool or not Path(tool).is_file() or not shutil.which("qemu-img"):
            raise ValueError("Install qemu and e2fsprogs (debugfs), or set DEBUGFS to its executable")
        image = download(LOCK["android"], downloads)
        android_inputs(image, android, tool)
    env = {"BACHATA_DIR": ps4, "HUSK_FEX_DIR": fex, "HUSK_D8": r8,
           "HUSK_ANDROID_INPUTS": android}
    env_path = source / "dependencies.env"
    env_path.write_text("# Generated by prepare_dependencies.py; source from bash.\n" +
                        "".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in env.items()))
    print(f"Prepared pinned sources. Environment: {env_path}")
    if args.sources_only:
        print("Android image extraction was skipped (--sources-only).")


if __name__ == "__main__":
    main()
