#!/usr/bin/env python3
"""Workbench for translating an MKV's English subtitles into other languages.

The translating itself is done by the agent; this script handles the mechanics
so timing can never drift and nothing gets lost:

  extract  MKV|SRT [--track ID] [--work DIR]   pull the English text sub out, cut it into chunks
  status   WORK                                what is done per language
  show     WORK --lang cze --part 3            print one chunk with context, ready to translate
  check    WORK --lang cze [--part 3]          validate translated parts (ids, tags, length, speed)
  build    WORK --lang cze                     merge parts -> <name>.<lang>.srt with original timings
  pack     WORK --langs cze,slo [...]          mux the built SRTs into the MKV (uses the mkv-tracks skill)

Stdlib only. Needs MKVToolNix (mkvmerge/mkvextract); ffmpeg only to convert ASS/WebVTT sources.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

LANGS = {  # ISO 639-2 (mkvmerge) -> (English name, native name, 2-letter for file names)
    "cze": ("Czech", "Čeština", "cs"), "slo": ("Slovak", "Slovenčina", "sk"),
    "ger": ("German", "Deutsch", "de"), "pol": ("Polish", "Polski", "pl"),
    "hun": ("Hungarian", "Magyar", "hu"), "fre": ("French", "Français", "fr"),
    "spa": ("Spanish", "Español", "es"), "ita": ("Italian", "Italiano", "it"),
    "por": ("Portuguese", "Português", "pt"), "dut": ("Dutch", "Nederlands", "nl"),
    "ukr": ("Ukrainian", "Українська", "uk"), "rus": ("Russian", "Русский", "ru"),
    "hrv": ("Croatian", "Hrvatski", "hr"), "slv": ("Slovenian", "Slovenščina", "sl"),
    "srp": ("Serbian", "Srpski", "sr"), "rum": ("Romanian", "Română", "ro"),
    "swe": ("Swedish", "Svenska", "sv"), "dan": ("Danish", "Dansk", "da"),
    "nor": ("Norwegian", "Norsk", "no"), "fin": ("Finnish", "Suomi", "fi"),
    "tur": ("Turkish", "Türkçe", "tr"), "gre": ("Greek", "Ελληνικά", "el"),
    "jpn": ("Japanese", "日本語", "ja"), "kor": ("Korean", "한국어", "ko"),
    "chi": ("Chinese", "中文", "zh"),
}
ALIASES = {"cz": "cze", "cs": "cze", "ces": "cze", "sk": "slo", "slk": "slo", "de": "ger", "deu": "ger",
           "pl": "pol", "hu": "hun", "fr": "fre", "fra": "fre", "es": "spa", "it": "ita", "pt": "por",
           "nl": "dut", "nld": "dut", "uk": "ukr", "ru": "rus", "hr": "hrv", "sl": "slv", "sr": "srp",
           "ro": "rum", "ron": "rum", "sv": "swe", "da": "dan", "no": "nor", "fi": "fin", "tr": "tur",
           "el": "gre", "ell": "gre", "ja": "jpn", "ko": "kor", "zh": "chi", "zho": "chi"}
CJK = {"jpn", "kor", "chi"}

MAX_LINE = 42        # characters per line (Netflix-style limit for Latin scripts)
MAX_LINES = 2
MAX_CPS = 20         # hard warning; aim for <= 17
CHUNK = 120          # target cues per part
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
TAG = re.compile(r"</?\s*([ibu]|font)\b[^>]*>", re.I)


def norm_lang(code: str) -> str:
    c = code.strip().lower()
    c = ALIASES.get(c, c)
    if not re.fullmatch(r"[a-z]{3}", c):
        sys.exit(f"ERROR: '{code}' is not a language code (use ISO 639-2 like cze, slo, ger).")
    return c


def lang_name(c: str) -> str:
    return LANGS.get(c, (c,))[0]


def find_tool(name: str, required: bool = True) -> str | None:
    for d in (r"C:\Program Files\MKVToolNix", r"C:\Program Files (x86)\MKVToolNix", "/usr/bin",
              "/usr/local/bin", "/opt/homebrew/bin", "/Applications/MKVToolNix.app/Contents/MacOS"):
        for cand in (Path(d) / f"{name}.exe", Path(d) / name):
            if cand.is_file():
                return str(cand)
    exe = shutil.which(name)
    if exe or not required:
        return exe
    sys.exit(f"ERROR: {name} not found. Install MKVToolNix (winget install MoritzBunkus.MKVToolNix).")


# ------------------------------------------------------------------ SRT

TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def to_ms(t: str) -> int:
    h, m, s, ms = TIME.match(t.strip()).groups()
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0"))


def fmt_ms(ms: int) -> str:
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def read_text(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp1250", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def parse_srt(text: str) -> list[dict]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        a, _, b = lines[ti].partition("-->")
        try:
            start, end = to_ms(a), to_ms(b.split()[0])
        except (AttributeError, IndexError):
            continue
        body = "\n".join(l.rstrip() for l in lines[ti + 1:]).strip()
        body = _CTRL.sub("", body)
        if body:
            cues.append({"start": start, "end": end, "text": body})
    cues.sort(key=lambda c: c["start"])
    for i, c in enumerate(cues, 1):
        c["id"] = i
    return cues


def write_srt(cues: list[dict], path: Path) -> None:
    out = [f"{i}\r\n{fmt_ms(c['start'])} --> {fmt_ms(c['end'])}\r\n{c['text'].replace(chr(10), chr(13) + chr(10))}\r\n"
           for i, c in enumerate(cues, 1)]
    path.write_text("\r\n".join(out), encoding="utf-8")


# ------------------------------------------------------------------ extract

def pick_track(info: dict, want: int | None) -> dict:
    subs = [t for t in info["tracks"] if t["type"] == "subtitles"]
    if want is not None:
        t = next((t for t in subs if t["id"] == want), None)
        if not t:
            sys.exit(f"ERROR: track {want} is not a subtitle track.")
        return t
    eng = [t for t in subs if (t["properties"].get("language") in ("eng", "en")
                               or str(t["properties"].get("language_ietf", "")).startswith("en"))]
    if not eng:
        listing = ", ".join(f"#{t['id']} {t['properties'].get('language')} {t['codec']}" for t in subs) or "none"
        sys.exit(f"ERROR: no English subtitle track. Subtitle tracks: {listing}. Use --track ID or --srt-style input.")
    text = [t for t in eng if t["codec"] not in ("HDMV PGS", "VobSub", "DVBSUB")
            and "PGS" not in t["codec"] and "VobSub" not in t["codec"]]
    if not text:
        sys.exit("ERROR: English subtitles here are image-based (PGS/VobSub) and need OCR first "
                 "(e.g. Subtitle Edit). Give me an English .srt instead.")

    def rank(t):
        p, name = t["properties"], (t["properties"].get("track_name") or "").lower()
        forced = p.get("forced_track") or "forced" in name
        sdh = p.get("flag_hearing_impaired") or bool(re.search(r"\b(sdh|cc|hi)\b", name))
        return (forced, sdh, -int(p.get("tag_number_of_frames") or p.get("num_index_entries") or 0))
    text.sort(key=rank)
    return text[0]


def chunk(cues: list[dict], size: int) -> list[tuple[int, int]]:
    """Split into ~size-cue parts, preferring a cut after a sentence end followed by a pause."""
    parts, start = [], 0
    while start < len(cues):
        end = min(start + size, len(cues))
        if end < len(cues):
            best = None
            for i in range(end, max(start + size // 2, start + 1), -1):
                prev, nxt = cues[i - 1], cues[i]
                if re.search(r"[.!?…\"”»]\s*$", prev["text"]) and nxt["start"] - prev["end"] >= 1500:
                    best = i
                    break
            end = best or end
        parts.append((cues[start]["id"], cues[end - 1]["id"]))
        start = end
    return parts


STYLE_TEMPLATE = """# Style sheet: {title}

Fill this in BEFORE translating (read the whole English source first). Every part is translated against it.

## What the film is
- Genre, tone, era/setting, rating (how rough is the language?):

## Characters  (name - gender - role - how they talk)
-

## Who addresses whom (formal vs informal "you" per pair; note when it changes)
-

## Terms & names  (keep / established translation)
-

## Decisions
- Units, swearing intensity, songs, on-screen text, SDH [sound] tags:
"""


def cmd_extract(a) -> None:
    src = Path(a.source).resolve()
    if not src.is_file():
        sys.exit(f"ERROR: not found: {src}")
    work = Path(a.work).resolve() if a.work else src.with_name(src.stem + ".subtrans")
    work.mkdir(parents=True, exist_ok=True)
    meta = {"source": str(src), "mkv": None, "track": None, "codec": None}

    if src.suffix.lower() in (".srt", ".ass", ".ssa", ".vtt"):
        sub_file = src
        mkv_guess = src.parent / (re.sub(r"(\.[a-z]{2,3})?$", "", src.stem, flags=re.I) + ".mkv")
        meta["mkv"] = str(mkv_guess) if mkv_guess.exists() else None
    else:
        info = json.loads(subprocess.run([find_tool("mkvmerge"), "-J", str(src)], capture_output=True,
                                         text=True, encoding="utf-8").stdout)
        t = pick_track(info, a.track)
        ext = {"SubRip/SRT": ".srt", "SubStationAlpha": ".ass", "WebVTT": ".vtt"}.get(t["codec"], ".srt")
        if "ASS" in t["codec"] or "SSA" in t["codec"]:
            ext = ".ass"
        sub_file = work / f"source.eng{ext}"
        print(f"Extracting track #{t['id']} ({t['codec']}, {t['properties'].get('track_name') or 'no name'}) "
              "- mkvextract reads the whole file, this can take minutes on an HDD...", flush=True)
        rc = subprocess.run([find_tool("mkvextract"), str(src), "tracks", f"{t['id']}:{sub_file}"],
                            stdout=subprocess.DEVNULL).returncode
        if rc >= 2 or not sub_file.exists():
            sys.exit("ERROR: mkvextract failed.")
        meta.update(mkv=str(src), track=t["id"], codec=t["codec"])

    if sub_file.suffix.lower() != ".srt":
        ff = shutil.which("ffmpeg") or sys.exit("ERROR: ffmpeg is needed to convert ASS/VTT to SRT.")
        conv = work / "source.eng.srt"
        subprocess.run([ff, "-loglevel", "error", "-y", "-i", str(sub_file), str(conv)], check=True)
        print("NOTE: converted to SRT - ASS styling/positioning is not kept.")
        sub_file = conv

    cues = parse_srt(read_text(sub_file))
    if not cues:
        sys.exit("ERROR: no cues found in the subtitle.")
    write_srt(cues, work / "source.eng.srt")
    (work / "source.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cues), encoding="utf-8")
    parts = chunk(cues, a.chunk)
    meta.update(cues=len(cues), parts=parts, duration_ms=cues[-1]["end"])
    (work / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    style = work / "style.md"
    if not style.exists():
        style.write_text(STYLE_TEMPLATE.format(title=Path(meta["mkv"] or src).stem), encoding="utf-8")
    (work / "source.txt").write_text("\n\n".join(f"#{c['id']}\n{c['text']}" for c in cues), encoding="utf-8")
    print(f"OK: {len(cues)} cues, {len(parts)} parts of ~{a.chunk} -> {work}")
    print(f"    read the whole film once: {work / 'source.txt'}")
    print(f"    then fill in:            {style}")


# ------------------------------------------------------------------ work dir helpers

def load(work: Path) -> tuple[dict, list[dict]]:
    work = Path(work).resolve()
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    cues = [json.loads(l) for l in (work / "source.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return meta, cues


def part_path(work: Path, lang: str, n: int) -> Path:
    return Path(work).resolve() / lang / f"part{n:02}.txt"


def parse_part(p: Path) -> tuple[dict[int, str], list[str]]:
    """'#12' header line, then the cue text until the next header."""
    out, errs, cur, buf = {}, [], None, []
    for line in read_text(p).replace("\r\n", "\n").split("\n"):
        m = re.fullmatch(r"\s*#(\d+)\s*", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur, buf = int(m.group(1)), []
            if cur in out:
                errs.append(f"#{cur} appears twice")
        elif cur is not None:
            buf.append(line.rstrip())
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return {k: _CTRL.sub("", v) for k, v in out.items()}, errs


def fmt_cue(c: dict) -> str:
    dur = (c["end"] - c["start"]) / 1000
    return f"#{c['id']}   [{fmt_ms(c['start'])[:-4]} · {dur:.1f}s]\n{c['text']}"


def cmd_show(a) -> None:
    meta, cues = load(a.work)
    lang = norm_lang(a.lang)
    n = a.part
    if not 1 <= n <= len(meta["parts"]):
        sys.exit(f"ERROR: parts are 1..{len(meta['parts'])}")
    lo, hi = meta["parts"][n - 1]
    by_id = {c["id"]: c for c in cues}
    work = Path(a.work).resolve()
    print(f"### Part {n}/{len(meta['parts'])} -> {lang_name(lang)} ({lang})   cues #{lo}-#{hi}")
    print(f"### Style sheet: {work / 'style.md'}  (re-read it if this is a new session)")
    if n > 1:
        prev = part_path(work, lang, n - 1)
        if prev.exists():
            done, _ = parse_part(prev)
            tail = sorted(done)[-8:]
            print("\n--- ALREADY TRANSLATED (previous part, for continuity - do not repeat) ---")
            for i in tail:
                print(f"#{i}  EN: {by_id[i]['text']}\n     {lang}: {done[i]}")
    print(f"\n--- TRANSLATE THESE ({hi - lo + 1} cues). Duration helps judge how much text fits. ---\n")
    print("\n\n".join(fmt_cue(by_id[i]) for i in range(lo, hi + 1)))
    ahead = [by_id[i] for i in range(hi + 1, min(hi + 6, len(cues) + 1))]
    if ahead:
        print("\n--- LOOKAHEAD (next part, context only - do not translate now) ---")
        print("\n".join(f"#{c['id']}  {c['text']}" for c in ahead))
    part_path(work, lang, n).parent.mkdir(exist_ok=True)
    print(f"\n### Write the result to: {part_path(work, lang, n)}")
    print("### Format: a '#<id>' line, then the translated text (1-2 lines), blank line between cues.")


# ------------------------------------------------------------------ check

COMMON_EN = re.compile(r"\b(the|you|and|what|is|are|this|that|with|have|don't|it's|i'm|okay|yeah)\b", re.I)


def visible(t: str) -> str:
    return TAG.sub("", re.sub(r"\{\\[^}]*\}", "", t))


def check_part(work: Path, lang: str, n: int, meta: dict, by_id: dict) -> tuple[list[str], list[str]]:
    p = part_path(work, lang, n)
    lo, hi = meta["parts"][n - 1]
    if not p.exists():
        return [f"part {n}: missing ({p.name})"], []
    tr, errs = parse_part(p)
    errs = [f"part {n}: {e}" for e in errs]
    warns = []
    want = set(range(lo, hi + 1))
    if missing := sorted(want - set(tr)):
        errs.append(f"part {n}: missing cues {compress(missing)}")
    if extra := sorted(set(tr) - want):
        errs.append(f"part {n}: cues not in this part {compress(extra)}")
    cjk = lang in CJK
    for i in sorted(want & set(tr)):
        t, src = tr[i], by_id[i]
        if not t:
            errs.append(f"#{i}: empty")
            continue
        if [m.group(0).lower().replace(" ", "") for m in TAG.finditer(t)].count("<i>") != \
           [m.group(0).lower().replace(" ", "") for m in TAG.finditer(t)].count("</i>"):
            errs.append(f"#{i}: unbalanced <i> tags")
        if bool(re.search(r"<i>", src["text"], re.I)) != bool(re.search(r"<i>", t, re.I)):
            warns.append(f"#{i}: italics differ from source (source {'has' if '<i>' in src['text'].lower() else 'has no'} <i>)")
        lines = visible(t).split("\n")
        if len(lines) > MAX_LINES:
            warns.append(f"#{i}: {len(lines)} lines (max {MAX_LINES})")
        for l in lines:
            if not cjk and len(l) > MAX_LINE:
                warns.append(f"#{i}: line {len(l)} chars > {MAX_LINE}: {l[:50]}…")
        dur = max((src["end"] - src["start"]) / 1000, 0.3)
        cps = len(visible(t).replace("\n", " ")) / dur
        if not cjk and cps > MAX_CPS:
            warns.append(f"#{i}: {cps:.0f} chars/s in {dur:.1f}s - shorten")
        if visible(t).strip().lower() == visible(src["text"]).strip().lower() and len(t) > 12:
            warns.append(f"#{i}: identical to English - untranslated?")
        elif len(COMMON_EN.findall(visible(t))) >= 3:
            warns.append(f"#{i}: looks like English is left in: {t[:50]}")
    return errs, warns


def compress(ids: list[int]) -> str:
    out, s = [], None
    for k, i in enumerate(ids):
        if s is None:
            s = i
        if k + 1 == len(ids) or ids[k + 1] != i + 1:
            out.append(f"#{s}" if s == i else f"#{s}-#{i}")
            s = None
    return ", ".join(out[:12]) + (" …" if len(out) > 12 else "")


def cmd_check(a) -> int:
    meta, cues = load(a.work)
    lang = norm_lang(a.lang)
    by_id = {c["id"]: c for c in cues}
    parts = [a.part] if a.part else range(1, len(meta["parts"]) + 1)
    all_e, all_w = [], []
    for n in parts:
        e, w = check_part(Path(a.work), lang, n, meta, by_id)
        all_e += e
        all_w += w
    for e in all_e:
        print("ERROR  " + e)
    for w in all_w[: a.max_warnings]:
        print("warn   " + w)
    if len(all_w) > a.max_warnings:
        print(f"warn   … {len(all_w) - a.max_warnings} more")
    print(f"{lang}: {len(all_e)} errors, {len(all_w)} warnings")
    return 1 if all_e else 0


def cmd_status(a) -> None:
    meta, cues = load(a.work)
    work = Path(a.work).resolve()
    print(f"{Path(meta['mkv'] or meta['source']).name}: {meta['cues']} cues, {len(meta['parts'])} parts")
    style = (work / "style.md").read_text(encoding="utf-8")
    print(f"style.md: {'filled in' if len(style) > len(STYLE_TEMPLATE) + 200 else 'NOT filled in yet'}")
    for d in sorted(p for p in work.iterdir() if p.is_dir() and re.fullmatch(r"[a-z]{3}", p.name)):
        done = [n for n in range(1, len(meta["parts"]) + 1) if part_path(work, d.name, n).exists()]
        built = list(work.glob(f"*.{d.name}.srt"))
        print(f"  {d.name} {lang_name(d.name):<10} parts {len(done)}/{len(meta['parts'])}"
              f"{'  next: part ' + str(next(n for n in range(1, len(meta['parts']) + 1) if n not in done)) if len(done) < len(meta['parts']) else ''}"
              f"{'  built: ' + built[0].name if built else ''}")


# ------------------------------------------------------------------ build & pack

def out_srt(work: Path, meta: dict, lang: str) -> Path:
    stem = Path(meta["mkv"] or meta["source"]).stem
    return Path(work).resolve() / f"{stem}.{lang}.srt"


def cmd_build(a) -> int:
    meta, cues = load(a.work)
    lang = norm_lang(a.lang)
    by_id = {c["id"]: c for c in cues}
    errs = []
    tr = {}
    for n in range(1, len(meta["parts"]) + 1):
        e, _ = check_part(Path(a.work), lang, n, meta, by_id)
        errs += e
        if part_path(a.work, lang, n).exists():
            tr.update(parse_part(part_path(a.work, lang, n))[0])
    if errs:
        print("\n".join("ERROR  " + e for e in errs[:30]))
        print("Fix the errors above (run `check`), then build again.")
        return 1
    out = [{"start": c["start"], "end": c["end"], "text": tr[c["id"]]} for c in cues]
    dest = out_srt(Path(a.work), meta, lang)
    write_srt(out, dest)
    print(f"OK: {dest}  ({len(out)} cues, timings identical to the English source)")
    return 0


def cmd_pack(a) -> int:
    meta, _ = load(a.work)
    if not meta.get("mkv"):
        sys.exit("ERROR: no MKV known for this work dir (source was a loose .srt). Use the mkv-tracks skill directly.")
    langs = [norm_lang(x) for x in a.langs.split(",")]
    default = norm_lang(a.default) if a.default else None
    mkvtracks = Path(__file__).resolve().parents[2] / "mkv-tracks" / "scripts" / "mkvtracks.py"
    if not mkvtracks.exists():
        sys.exit(f"ERROR: the mkv-tracks skill is needed for packing (expected {mkvtracks}). "
                 "Install it: npx skills add depthark/mkv-tracks")
    cmd = [sys.executable, str(mkvtracks), "pack", meta["mkv"]]
    for lang in langs:
        srt = out_srt(Path(a.work), meta, lang)
        if not srt.exists():
            sys.exit(f"ERROR: {srt.name} not built yet - run: build --lang {lang}")
        opts = f"lang={lang};name={LANGS.get(lang, (lang, lang))[1]}" + (";default" if lang == default else "")
        cmd += ["--add", f"{srt}::{opts}"]
    if meta.get("track") is not None:  # the MKV may have been remuxed since extract: re-verify the id
        info = json.loads(subprocess.run([find_tool("mkvmerge"), "-J", meta["mkv"]], capture_output=True,
                                         text=True, encoding="utf-8").stdout)
        t = next((t for t in info["tracks"] if t["id"] == meta["track"]), None)
        if not t or t["type"] != "subtitles" or not str(t["properties"].get("language", "")).startswith("en"):
            sys.exit(f"ERROR: track #{meta['track']} is no longer the English subtitle (file changed since "
                     "extract). Re-run extract, or use the mkv-tracks skill to pick tracks by hand.")
    if a.drop_english:
        if meta.get("track") is None:
            sys.exit("ERROR: don't know which track is English; use the mkv-tracks skill to drop it.")
        cmd += ["--drop", str(meta["track"])]
    elif default and meta.get("track") is not None:
        cmd += ["--set", f"{meta['track']}::default=0"]
    if a.replace:
        cmd.append("--replace")
    if a.dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd).returncode


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("extract")
    p.add_argument("source", help=".mkv (English track is picked automatically) or an English .srt/.ass/.vtt")
    p.add_argument("--track", type=int, help="mkvmerge track id of the subtitle to translate")
    p.add_argument("--work", help="work dir (default: <name>.subtrans next to the source)")
    p.add_argument("--chunk", type=int, default=CHUNK)
    p.set_defaults(func=cmd_extract)

    p = sp.add_parser("status")
    p.add_argument("work")
    p.set_defaults(func=cmd_status)

    p = sp.add_parser("show")
    p.add_argument("work")
    p.add_argument("--lang", required=True)
    p.add_argument("--part", type=int, required=True)
    p.set_defaults(func=cmd_show)

    p = sp.add_parser("check")
    p.add_argument("work")
    p.add_argument("--lang", required=True)
    p.add_argument("--part", type=int)
    p.add_argument("--max-warnings", type=int, default=40)
    p.set_defaults(func=cmd_check)

    p = sp.add_parser("build")
    p.add_argument("work")
    p.add_argument("--lang", required=True)
    p.set_defaults(func=cmd_build)

    p = sp.add_parser("pack")
    p.add_argument("work")
    p.add_argument("--langs", required=True, help="comma list, e.g. cze,slo")
    p.add_argument("--default", help="language whose track becomes the default subtitle")
    p.add_argument("--drop-english", action="store_true", help="remove the English source track (replace it)")
    p.add_argument("--replace", action="store_true", help="swap the result in place of the original MKV")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_pack)

    a = ap.parse_args()
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
