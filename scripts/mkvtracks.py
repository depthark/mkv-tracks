#!/usr/bin/env python3
"""Inspect and repack MKV files with mkvmerge (MKVToolNix).

  analyze PATH [--json]           list tracks of an .mkv (or every .mkv in a folder)
                                  plus external subtitle files found next to it
  pack MKV [options]              remux: drop tracks, edit flags, add external subs

Stdlib only. Needs mkvmerge (MKVToolNix) on PATH or in its default install dir.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sup", ".idx", ".sub"}
TEXT_SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
MAX_SNIFF = 4 * 1024 * 1024  # bytes read for charset/language sniffing
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def clean(v):
    """Strip control / bidi chars from untrusted metadata (track names, titles, file names)."""
    return _CTRL.sub("", v) if isinstance(v, str) else v

# filename token -> ISO 639-2 (what mkvmerge uses)
LANG_TOKENS = {
    "cz": "cze", "cs": "cze", "cze": "cze", "ces": "cze", "czech": "cze", "cesky": "cze",
    "sk": "slo", "slo": "slo", "slk": "slo", "slovak": "slo",
    "en": "eng", "eng": "eng", "english": "eng",
    "de": "ger", "ger": "ger", "deu": "ger", "german": "ger",
    "pl": "pol", "pol": "pol", "polish": "pol",
    "hu": "hun", "hun": "hun", "hungarian": "hun",
    "fr": "fre", "fre": "fre", "fra": "fre", "french": "fre",
    "es": "spa", "spa": "spa", "spanish": "spa",
    "it": "ita", "ita": "ita", "italian": "ita",
    "ru": "rus", "rus": "rus", "russian": "rus",
    "uk": "ukr", "ukr": "ukr", "ukrainian": "ukr",
}
LANG_NAMES = {
    "cze": "Czech", "slo": "Slovak", "eng": "English", "ger": "German", "pol": "Polish",
    "hun": "Hungarian", "fre": "French", "spa": "Spanish", "ita": "Italian",
    "rus": "Russian", "ukr": "Ukrainian",
}


def find_tool(name: str) -> str:
    exe = shutil.which(name)
    if exe:
        return exe
    for d in (r"C:\Program Files\MKVToolNix", r"C:\Program Files (x86)\MKVToolNix",
              "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin",
              "/Applications/MKVToolNix.app/Contents/MacOS"):
        for cand in (Path(d) / f"{name}.exe", Path(d) / name):
            if cand.is_file():
                return str(cand)
    sys.exit(f"ERROR: {name} not found. Install MKVToolNix "
             "(winget install MoritzBunkus.MKVToolNix / apt install mkvtoolnix / brew install mkvtoolnix).")


def identify(mkv: Path) -> dict:
    out = subprocess.run([find_tool("mkvmerge"), "-J", str(mkv)],
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode >= 2:
        sys.exit(f"ERROR: mkvmerge could not read {mkv}:\n{out.stdout}{out.stderr}")
    return json.loads(out.stdout)


# ---------------------------------------------------------------- external subs

def read_text(p: Path) -> tuple[str, str]:
    with open(p, "rb") as f:
        raw = f.read(MAX_SNIFF)
    for enc in ("utf-8-sig", "utf-16"):
        if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            continue
        try:
            return raw.decode(enc), ("UTF-8" if enc == "utf-8-sig" else "UTF-16")
        except UnicodeDecodeError:
            pass
    # legacy 8-bit: cp1250 if it looks Central European, else cp1252
    t1250 = raw.decode("cp1250", errors="replace")
    if sum(t1250.count(c) for c in "ěščřžůťďňľĺŕĚŠČŘŽŮŤĎŇĽ") > 5:
        return t1250, "cp1250"
    return raw.decode("cp1252", errors="replace"), "cp1252"


def guess_lang_from_text(text: str) -> str | None:
    t = text[:200_000]
    cz = sum(t.count(c) for c in "ěřůĚŘŮ")
    sk = sum(t.count(c) for c in "ľĺŕôäĽĹŔÔÄ")
    if cz + sk > 10:
        return "cze" if cz >= sk else "slo"
    if sum(t.count(c) for c in "ąęłśźżńĄĘŁŚŹŻŃ") > 10:
        return "pol"
    if sum(t.count(c) for c in "őűŐŰ") > 10:
        return "hun"
    if sum(t.count(c) for c in "äöüßÄÖÜ") > 20:
        return "ger"
    if len(re.findall(r"[а-яА-Я]", t)) > 50:
        return "ukr" if sum(t.count(c) for c in "іїєґІЇЄҐ") > 5 else "rus"
    words = re.findall(r"\b(the|you|and|what|is|to)\b", t, re.I)
    if len(words) > 30:
        return "eng"
    return None


def guess_lang_from_name(p: Path, mkv_stem: str) -> str | None:
    rest = p.stem[len(mkv_stem):] if p.stem.lower().startswith(mkv_stem.lower()) else p.stem
    for tok in reversed(re.split(r"[.\-_ \[\]()]+", rest.lower())):
        if tok in LANG_TOKENS:
            return LANG_TOKENS[tok]
    return None


def describe_external(p: Path, mkv_stem: str) -> dict:
    info = {"path": str(p), "file": clean(p.name), "format": p.suffix.lower().lstrip("."),
            "size": p.stat().st_size, "encoding": None, "language": None,
            "language_source": None, "forced_hint": bool(re.search(r"forced", p.stem, re.I)),
            "sdh_hint": bool(re.search(r"\b(sdh|hi|cc)\b", p.stem, re.I))}
    lang = guess_lang_from_name(p, mkv_stem)
    if lang:
        info["language"], info["language_source"] = lang, "filename"
    if p.suffix.lower() in TEXT_SUB_EXTS:
        text, enc = read_text(p)
        info["encoding"] = enc
        info["cues"] = len(re.findall(r"-->", text)) or None
        if not lang:
            lang = guess_lang_from_text(text)
            if lang:
                info["language"], info["language_source"] = lang, "content"
    return info


def find_externals(mkv: Path, all_in_folder: bool) -> list[dict]:
    stem = mkv.stem
    subs = []
    for p in sorted(mkv.parent.iterdir()):
        if p.suffix.lower() not in SUB_EXTS or not p.is_file():
            continue
        if p.suffix.lower() == ".sub" and p.with_suffix(".idx").exists():
            continue  # VobSub: the .idx is the entry point
        if all_in_folder or p.stem.lower().startswith(stem.lower()[: max(8, len(stem) // 2)]):
            subs.append(describe_external(p, stem))
    # Subs/ or Subtitles/ subfolder
    for sd in ("Subs", "subs", "Subtitles", "subtitles"):
        d = mkv.parent / sd
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.suffix.lower() in SUB_EXTS and p.is_file() and not p.is_symlink():
                    subs.append(describe_external(p, stem))
    return subs


# ---------------------------------------------------------------- analyze

def fmt_size(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def track_summary(t: dict) -> dict:
    p = t.get("properties", {})
    s = {"id": t["id"], "type": t["type"], "codec": t.get("codec"),
         "language": p.get("language_ietf") or p.get("language"),
         "name": clean(p.get("track_name")), "default": p.get("default_track", False),
         "forced": p.get("forced_track", False)}
    if t["type"] == "video":
        s["detail"] = p.get("pixel_dimensions")
    elif t["type"] == "audio":
        ch = p.get("audio_channels")
        s["detail"] = f"{ch}ch {p.get('audio_sampling_frequency', '')}Hz".strip() if ch else None
    else:
        s["detail"] = None
    if p.get("flag_hearing_impaired"):
        s["sdh"] = True
    if p.get("flag_commentary"):
        s["commentary"] = True
    return s


def analyze_one(mkv: Path, all_in_folder: bool) -> dict:
    d = identify(mkv)
    return {"file": str(mkv), "size": mkv.stat().st_size,
            "title": clean(d.get("container", {}).get("properties", {}).get("title")),
            "tracks": [track_summary(t) for t in d.get("tracks", [])],
            "attachments": [{"id": a["id"], "file_name": clean(a.get("file_name")),
                             "content_type": a.get("content_type")} for a in d.get("attachments", [])],
            "chapters": sum(c.get("num_entries", 0) for c in d.get("chapters", [])),
            "external_subtitles": find_externals(mkv, all_in_folder)}


def print_report(r: dict) -> None:
    print(f"\n=== {Path(r['file']).name}  ({fmt_size(r['size'])})")
    if r["title"]:
        print(f"    title: {r['title']}")
    print(f"    {'ID':>3}  {'TYPE':<9} {'CODEC':<22} {'LANG':<7} {'FLAGS':<16} NAME / DETAIL")
    for t in r["tracks"]:
        flags = ",".join(f for f, on in (("default", t["default"]), ("forced", t["forced"]),
                                          ("sdh", t.get("sdh")), ("comment", t.get("commentary"))) if on)
        extra = " | ".join(x for x in (t["name"], t["detail"]) if x)
        print(f"    {t['id']:>3}  {t['type']:<9} {str(t['codec'])[:22]:<22} {str(t['language'] or '-'):<7} "
              f"{flags or '-':<16} {extra}")
    if r["attachments"]:
        print(f"    attachments: {len(r['attachments'])} ({', '.join(a['file_name'] or '?' for a in r['attachments'][:5])})")
    if r["chapters"]:
        print(f"    chapters: {r['chapters']}")
    ext = r["external_subtitles"]
    print(f"    external subtitles: {len(ext) or 'none'}")
    for e in ext:
        lang = f"{e['language']} (from {e['language_source']})" if e["language"] else "unknown"
        bits = [e["format"], lang, e["encoding"] or "", f"{e['cues']} cues" if e.get("cues") else "",
                "forced?" if e["forced_hint"] else "", "SDH?" if e["sdh_hint"] else ""]
        print(f"      - {e['file']}  [{', '.join(b for b in bits if b)}]")


def cmd_analyze(a) -> None:
    target = Path(a.path).resolve()
    mkvs = sorted(target.glob("*.mkv")) if target.is_dir() else [target]
    if not mkvs:
        sys.exit(f"ERROR: no .mkv files in {target}")
    reports = [analyze_one(m, a.all_subs or (target.is_dir() and len(mkvs) == 1)) for m in mkvs]
    if a.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0], ensure_ascii=False, indent=2))
    else:
        for r in reports:
            print_report(r)


# ---------------------------------------------------------------- pack

def parse_opts(spec: str) -> tuple[str, dict]:
    """'PATH::lang=cze;name=Czech;default' -> (PATH, {...}). '::' keeps Windows drive colons safe."""
    path, _, rest = spec.partition("::")
    opts = {}
    for part in filter(None, (x.strip() for x in rest.split(";"))):
        k, eq, v = part.partition("=")
        opts[k.strip().lower()] = v.strip() if eq else "1"
    return path, opts


def yes(v: str | None) -> bool:
    return str(v).lower() in ("1", "yes", "true", "y", "on")


def cmd_pack(a) -> None:
    mkv = Path(a.mkv).resolve()
    info = identify(mkv)
    tracks = {t["id"]: t for t in info["tracks"]}

    drop = set()
    for x in a.drop or []:
        drop.update(int(i) for i in str(x).split(",") if i.strip())
    if a.keep:
        keep = {int(i) for x in a.keep for i in str(x).split(",") if i.strip()}
        drop |= set(tracks) - keep
    unknown = drop - set(tracks)
    if unknown:
        sys.exit(f"ERROR: no such track id(s): {sorted(unknown)}")

    adds = [parse_opts(s) for s in a.add or []]
    adds = [(str(Path(path).resolve()), o) for path, o in adds]
    for path, _ in adds:
        if not Path(path).is_file():
            sys.exit(f"ERROR: subtitle not found: {path}")
    edits = {}
    for s in a.set or []:
        tid, opts = parse_opts(s)
        edits[int(tid)] = opts

    kept = [t for tid, t in tracks.items() if tid not in drop]
    if not any(t["type"] == "video" for t in kept):
        sys.exit("ERROR: refusing to drop every video track.")

    # Who ends up default among subtitles? If an added sub is marked default,
    # clear default on every kept internal subtitle so players pick the new one.
    new_default_sub = any(yes(o.get("default")) for _, o in adds)
    new_default_audio = [tid for tid, o in edits.items()
                         if yes(o.get("default")) and tracks.get(tid, {}).get("type") == "audio"]

    out = Path(a.output).resolve() if a.output else mkv.with_name(mkv.stem + ".repack.mkv")
    if out == mkv:
        sys.exit("ERROR: --output must differ from the source; use --replace to swap in place.")
    if a.replace:
        out = mkv.with_name(mkv.stem + ".tmp-mkvtracks.mkv")

    cmd = [find_tool("mkvmerge"), "-o", str(out)]
    if a.title is not None:
        cmd += ["--title", a.title]
    for kind, flag in (("video", "--video-tracks"), ("audio", "--audio-tracks"),
                       ("subtitles", "--subtitle-tracks")):
        ids = [tid for tid, t in tracks.items() if t["type"] == kind]
        if not ids:
            continue
        keep_ids = [str(i) for i in ids if i not in drop]
        if not keep_ids:
            cmd.append({"video": "--no-video", "audio": "--no-audio", "subtitles": "--no-subtitles"}[kind])
        elif len(keep_ids) < len(ids):
            cmd += [flag, ",".join(keep_ids)]
    if a.no_attachments:
        cmd.append("--no-attachments")
    if a.no_chapters:
        cmd.append("--no-chapters")

    for tid, t in tracks.items():
        if tid in drop:
            continue
        o = edits.get(tid, {})
        if "lang" in o:
            cmd += ["--language", f"{tid}:{o['lang']}"]
        if "name" in o:
            cmd += ["--track-name", f"{tid}:{o['name']}"]
        if "default" in o:
            cmd += ["--default-track-flag", f"{tid}:{'1' if yes(o['default']) else '0'}"]
        elif (t["type"] == "subtitles" and new_default_sub) or \
             (t["type"] == "audio" and new_default_audio and tid not in new_default_audio):
            cmd += ["--default-track-flag", f"{tid}:0"]
        if "forced" in o:
            cmd += ["--forced-display-flag", f"{tid}:{'1' if yes(o['forced']) else '0'}"]
    cmd.append(str(mkv))

    for path, o in adds:
        p = Path(path)
        ext = describe_external(p, mkv.stem)
        lang = o.get("lang") or ext["language"] or "und"
        name = o.get("name") if "name" in o else LANG_NAMES.get(lang, "")
        if p.suffix.lower() in TEXT_SUB_EXTS:
            enc = o.get("charset") or ext["encoding"]
            if enc and enc not in ("UTF-8",):
                cmd += ["--sub-charset", f"0:{enc}"]
        cmd += ["--language", f"0:{lang}"]
        if name:
            cmd += ["--track-name", f"0:{name}"]
        cmd += ["--default-track-flag", f"0:{'1' if yes(o.get('default')) else '0'}"]
        if yes(o.get("forced")):
            cmd += ["--forced-display-flag", "0:1"]
        if yes(o.get("sdh")):
            cmd += ["--hearing-impaired-flag", "0:1"]
        if "delay" in o:  # milliseconds, may be negative
            cmd += ["--sync", f"0:{int(o['delay'])}"]
        cmd.append(str(p))

    print("mkvmerge command:\n  " + subprocess.list2cmdline(cmd), flush=True)
    if a.dry_run:
        return

    free = shutil.disk_usage(out.parent).free
    if free < mkv.stat().st_size * 1.05:
        sys.exit(f"ERROR: not enough free space on target drive ({fmt_size(free)} free, "
                 f"need ~{fmt_size(mkv.stat().st_size)}).")

    backup = mkv.with_name(mkv.stem + ".orig-mkvtracks.mkv")
    if a.replace and backup.exists():
        sys.exit(f"ERROR: {backup} already exists (left over from an earlier run?); move it away and re-run.")

    rc = subprocess.run(cmd).returncode
    if rc >= 2:
        if out.exists():
            out.unlink()
        sys.exit(f"ERROR: mkvmerge failed (exit {rc}); source untouched.")
    if rc == 1:
        print("NOTE: mkvmerge finished with warnings (exit 1) — output was written.")

    # verify
    res = identify(out)
    expected = len(kept) + len(adds)
    got = len(res["tracks"])
    if got != expected:
        sys.exit(f"ERROR: verification failed — expected {expected} tracks, got {got}. "
                 f"Kept output at {out}, source untouched.")

    if a.replace:
        backup = mkv.with_name(mkv.stem + ".orig-mkvtracks.mkv")
        os.replace(mkv, backup)
        try:
            os.replace(out, mkv)
        except OSError:
            os.replace(backup, mkv)
            raise
        backup.unlink()
        out = mkv
        print(f"Replaced original: {mkv}")

    if a.delete_external:
        for path, _ in adds:
            Path(path).unlink()
            print(f"Deleted external: {path}")

    print(f"\nDONE -> {out}")
    print_report({"file": str(out), "size": out.stat().st_size,
                  "title": res.get("container", {}).get("properties", {}).get("title"),
                  "tracks": [track_summary(t) for t in res["tracks"]],
                  "attachments": res.get("attachments", []) and [
                      {"file_name": x.get("file_name")} for x in res["attachments"]],
                  "chapters": sum(c.get("num_entries", 0) for c in res.get("chapters", [])),
                  "external_subtitles": []})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    an = sp.add_parser("analyze", help="list tracks + external subtitles")
    an.add_argument("path", help=".mkv file or folder")
    an.add_argument("--json", action="store_true")
    an.add_argument("--all-subs", action="store_true",
                    help="treat every subtitle file in the folder as a candidate, not just name-matched ones")
    an.set_defaults(func=cmd_analyze)

    pk = sp.add_parser("pack", help="remux with selected tracks and added subtitles")
    pk.add_argument("mkv")
    pk.add_argument("--drop", action="append", help="track ids to remove, e.g. 3,4 (repeatable)")
    pk.add_argument("--keep", action="append", help="keep ONLY these track ids (alternative to --drop)")
    pk.add_argument("--add", action="append",
                    help="external sub: 'PATH::lang=cze;name=Czech;default;forced;sdh;delay=-500;charset=cp1250'")
    pk.add_argument("--set", action="append",
                    help="edit kept track: 'ID::lang=eng;name=English;default=1;forced=0'")
    pk.add_argument("--title", help="set container title ('' clears it)")
    pk.add_argument("--no-attachments", action="store_true")
    pk.add_argument("--no-chapters", action="store_true")
    pk.add_argument("-o", "--output", help="output path (default: <name>.repack.mkv)")
    pk.add_argument("--replace", action="store_true", help="swap the result in place of the original after verify")
    pk.add_argument("--delete-external", action="store_true", help="delete added subtitle files after success")
    pk.add_argument("--dry-run", action="store_true", help="print the mkvmerge command only")
    pk.set_defaults(func=cmd_pack)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
