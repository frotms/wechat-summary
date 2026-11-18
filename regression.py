#!/usr/bin/env python
"""Real-data end-to-end regression for the wechat-chatlog skill.

It exports a small recent slice from a real account, runs the deep verifier,
and checks that exported media has a valid container header.  The export is
written to a temporary directory and removed by default.

Usage:
    python regression.py --account <account-dir> [--session "<name>"]
    python regression.py --account <account-dir> --session 123@chatroom --keep
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WF = os.path.join(HERE, "wfchat.py")

IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"BM")


def run(args, check=True):
    p = subprocess.run(
        [sys.executable, WF] + args,
        cwd=HERE, text=True, encoding="utf-8", errors="replace",
        capture_output=True,
    )
    if check and p.returncode != 0:
        sys.stderr.write(p.stdout + "\n" + p.stderr)
        raise SystemExit(f"command failed: {' '.join(args)}")
    return p


def pick_sessions(account):
    p = run(["--account", account, "sessions", "--limit", "40"])
    data = json.loads(p.stdout)
    rows = data.get("sessions") or []
    # Prefer groups/private chats with recent non-system activity, but let the
    # export retry loop below skip empty/system-only sessions.
    out = [r["username"] for r in rows]
    groups = [u for u in out if u.endswith("@chatroom")]
    rest = [u for u in out if not u.endswith("@chatroom")]
    return groups + rest


def check_media(out):
    bad = []
    for r, _ds, fs in os.walk(out):
        for f in fs:
            p = os.path.join(r, f)
            ext = os.path.splitext(f)[1].lower()
            try:
                head = open(p, "rb").read(16)
            except OSError:
                bad.append((p, "unreadable"))
                continue
            if ext in (".jpg", ".png", ".gif", ".webp", ".bmp"):
                if not head.startswith(IMAGE_MAGIC):
                    # WXGF may legitimately remain raw if it is true HEVC.
                    if ext != ".wxgf" and not head.startswith(b"\x41\x98\x86\x8f"):
                        bad.append((p, "image magic"))
            elif ext == ".mp4":
                if b"ftyp" not in head:
                    bad.append((p, "mp4 magic"))
            elif ext == ".wav":
                if not head.startswith(b"RIFF"):
                    bad.append((p, "wav magic"))
            elif ext == ".silk":
                if not (head.startswith(b"#!SILK") or head.startswith(b"\x02#!SILK")):
                    bad.append((p, "silk magic"))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--session", default=None)
    ap.add_argument("--last", type=int, default=50)
    ap.add_argument("--types", default="text,image,file,voice,sticker,app")
    ap.add_argument("--out", default=None)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    out = os.path.abspath(args.out or tempfile.mkdtemp(prefix="wxs_regression_"))
    os.makedirs(out, exist_ok=True)
    candidates = [args.session] if args.session else pick_sessions(args.account)[:12]
    if not candidates:
        raise SystemExit("no sessions found")

    summary = None
    used = None
    for cand in candidates:
        p = run(["--account", args.account, "export", "--session", cand,
                 "--last", str(args.last), "--types", args.types,
                 "--voice-format", "both", "--format", "json", "--out", out],
                check=False)
        if p.returncode != 0:
            continue
        try:
            s = json.loads(p.stdout)
        except Exception:
            continue
        if s.get("messages", 0) > 0:
            summary, used = s, cand
            break
        # Empty/system-only session: clear and try the next candidate.
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out, exist_ok=True)
    if not summary:
        raise SystemExit("no session with exportable messages found")
    print(f"account={args.account} session={used}", flush=True)
    print(f"export: {summary.get('sessions', 0)} session(s), "
          f"{summary.get('messages', 0)} message(s)", flush=True)

    p = run(["--account", args.account, "verify", "--out", out, "--deep"])
    result = json.loads(p.stdout)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=1))
        print("DEEP VERIFY FAILED", file=sys.stderr)
        return 1

    bad = check_media(out)
    if bad:
        for pth, why in bad[:20]:
            print(f"bad media: {pth} ({why})", file=sys.stderr)
        print("MEDIA CHECK FAILED", file=sys.stderr)
        return 1

    print("OK: export + deep verify + media headers passed", flush=True)
    if not args.keep:
        shutil.rmtree(out, ignore_errors=True)
    else:
        print(f"kept output: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
