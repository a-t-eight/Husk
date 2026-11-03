# SPDX-License-Identifier: GPL-2.0-or-later
"""OpenSSL generates/authenticates fixtures; the actual Husk checker is the SUT."""
from pathlib import Path
import os
import subprocess
import sys

checker, directory = sys.argv[1:]
root = Path(directory)
openssl = os.environ.get("OPENSSL", "openssl")


def command(*args, **kwargs):
    return subprocess.run(args, capture_output=True, check=True, **kwargs)


def element(data, offset=0):
    """Return tag, content offset, and end for the definite-length test fixtures."""
    first = data[offset + 1]
    size = first & 127 if first & 128 else 0
    length = int.from_bytes(data[offset + 2:offset + 2 + size], "big") if size else first
    start = offset + 2 + size
    return data[offset], start, start + length


def tlv(tag, content):
    length = len(content)
    encoded = length.to_bytes((length.bit_length() + 7) // 8 or 1, "big")
    header = bytes([length]) if length < 128 else bytes([128 + len(encoded)]) + encoded
    return bytes([tag]) + header + content


failures = 0
checks = 0


def check(label, data, password, expected, oracle=None):
    global failures, checks
    checks += 1
    path = root / "case.p12"
    path.write_bytes(data)
    if oracle is not None:
        verify = subprocess.run([openssl, "pkcs12", "-in", str(path), "-noout", "-passin", "stdin"],
                                input=(password + "\n").encode(), capture_output=True)
        assert (verify.returncode == 0) == oracle, "OpenSSL fixture oracle disagrees: " + label
    result = command(checker, str(path), password)
    actual = int(result.stdout)
    passed = actual == expected
    failures += not passed
    print(f'{"PASS" if passed else "FAIL"} {label}: expected={expected} actual={actual}')


command(openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=Husk Synthetic MAC Test/OU=TESTTEAM01", "-keyout", str(root / "key.pem"),
        "-out", str(root / "cert.pem"))
fixtures = {}
for digest in ["sha1", "sha256"]:
    for label, password in [("protected", "test+password/42"), ("empty", ""), ("unicode", "tést-密碼")]:
        output = root / f"{digest}-{label}.p12"
        command(openssl, "pkcs12", "-export", "-inkey", str(root / "key.pem"),
                "-in", str(root / "cert.pem"), "-out", str(output), "-name", "Synthetic identity",
                "-keypbe", "PBE-SHA1-3DES", "-certpbe", "PBE-SHA1-3DES", "-macalg", digest,
                "-iter", "2048", "-passout", "stdin", input=(password + "\n").encode())
        data = output.read_bytes()
        fixtures[(digest, label)] = data
        check(f"{digest}/{label}: correct password", data, password, 1, oracle=True)
        check(f"{digest}/{label}: wrong password", data, "incorrect", 0, oracle=False)

        _, pfx_start, _ = element(data)
        _, _, version_end = element(data, pfx_start)
        _, ci_start, _ = element(data, version_end)
        _, _, oid_end = element(data, ci_start)
        _, explicit_start, _ = element(data, oid_end)
        _, auth_start, auth_end = element(data, explicit_start)
        tampered = bytearray(data)
        tampered[auth_start + (auth_end - auth_start) // 2] ^= 1
        check(f"{digest}/{label}: altered authenticated safe", tampered, password, 0, oracle=False)

data = fixtures[("sha256", "protected")]
password = "test+password/42"
_, pfx_start, pfx_end = element(data)
_, _, version_end = element(data, pfx_start)
_, ci_start, ci_end = element(data, version_end)
_, oid_start, oid_end = element(data, ci_start)
_, explicit_start, explicit_end = element(data, oid_end)
_, auth_start, auth_end = element(data, explicit_start)
for label, offset, value in [
    ("unsupported content type", oid_end - 1, 2),
    ("wrong ContentInfo tag", version_end, 0x31),
    ("wrong explicit wrapper tag", oid_end, 0xa1),
    ("wrong OCTET STRING tag", explicit_start, 0x05),
    ("truncated nested length", explicit_start + 1, 0x84),
]:
    mutated = bytearray(data)
    mutated[offset] = value
    check(label, mutated, password, -1)
for length in [1, len(data) - 1]:
    check(f"truncated PFX at {length}", data[:length], password, -1)

oid = data[ci_start:oid_end]
octets = data[explicit_start:explicit_end]
version = data[pfx_start:version_end]
mac = data[ci_end:pfx_end]
for label, content in [
    ("extra explicit-wrapper child", oid + tlv(0xa0, octets + b"\x05\x00")),
    ("extra ContentInfo child", data[ci_start:ci_end] + b"\x05\x00"),
]:
    check(label, tlv(0x30, version + tlv(0x30, content) + mac), password, -1)

# Preserve the existing no-MAC hint; final acceptance still belongs to Security.
check("no MAC retains existing hint", tlv(0x30, version + data[version_end:ci_end]), "", 1)
print(f"{checks - failures}/{checks} checks passed")
sys.exit(1 if failures else 0)
