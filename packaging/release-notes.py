#!/usr/bin/env python3
"""Merge one platform's SHA-256 block into a GitHub release's notes without clobbering the others.

    python packaging/release-notes.py TAG linux SHA256SUMS-linux.txt
    python packaging/release-notes.py TAG windows SHA256SUMS.txt

The Windows and Linux workflows run independently and both edit the same release body, so each owns
a block between <!-- name --> / <!-- /name --> markers. The edit is re-read and retried because the
two workflows can finish at the same moment (needs GH_TOKEN and the `gh` CLI).
"""
import re
import subprocess
import sys
import time

TITLES = {"linux": "Linux (x86_64, aarch64)", "windows": "Windows (unsigned — see the README for SmartScreen / Defender)"}


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")


def body_of(tag):
    # GitHub stores release bodies with CRLF line endings: normalise before comparing or editing.
    out = gh("release", "view", tag, "--json", "body", "--jq", ".body").stdout
    return out.replace("\r\n", "\n").strip()


def main(tag, name, sums_file):
    sums = open(sums_file, encoding="utf-8").read().strip()
    block = f"<!-- {name} -->\n**{TITLES.get(name, name)}** — SHA-256:\n```\n{sums}\n```\n<!-- /{name} -->"
    if gh("release", "view", tag).returncode != 0:
        gh("release", "create", tag, "--title", tag, "--notes", "")
    pat = re.compile(rf"<!-- {name} -->.*?<!-- /{name} -->", re.S)
    for attempt in range(6):
        body = body_of(tag)
        new = pat.sub(block, body) if pat.search(body) else (body + "\n\n" + block).strip()
        if new != body:
            gh("release", "edit", tag, "--notes", new)
        time.sleep(3 + attempt)
        if block in body_of(tag):
            return 0
    print("could not confirm release notes", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
