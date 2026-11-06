# mkv-tracks

Agent skills (Claude Code, Codex, and other agents that read `SKILL.md`) for MKV files:

| Skill | What it does |
|---|---|
| [`mkv-tracks`](skills/mkv-tracks/SKILL.md) | Shows the tracks in an MKV, removes the ones you don't want, packs external subtitles in, and fixes default/forced flags |
| [`mkv-subtitle-translate`](skills/mkv-subtitle-translate/SKILL.md) | Translates the English subtitles into Czech, Slovak or any other language **as a subtitler would**, then packs them back in, next to or instead of the English track |

## mkv-tracks

The skill:

- **analyzes** an `.mkv` file and lists its video, audio and subtitle tracks (codec, language, name, default/forced flags, channels, resolution), its chapters and attachments, and any external subtitle files sitting next to it, with their guessed language and encoding
- lets you **choose** which tracks to remove and which external subtitles to add, using the agent's checkbox UI
- **packs** the result with `mkvmerge`. Streams are copied, not re-encoded, so quality stays the same. The original file is replaced only after the output has been checked.

## Install

```bash
npx skills add depthark/mkv-tracks
```

This installs both skills. `mkv-subtitle-translate` uses `mkv-tracks` for packing, so keep them together. You can also copy `skills/<name>/` into your skills folder (`~/.agents/skills/` or `~/.claude/skills/`).

Requirements: **Python 3.9+** (standard library only) and **[MKVToolNix](https://mkvtoolnix.download/)**:

```bash
winget install MoritzBunkus.MKVToolNix   # Windows
sudo apt install mkvtoolnix              # Debian/Ubuntu
brew install mkvtoolnix                  # macOS
```

## Use

Ask your agent something like *"pack the subtitles into H:\Movies\Some Movie"* or *"what tracks does this mkv have?"*.

The script also works on its own:

```bash
python skills/mkv-tracks/scripts/mkvtracks.py analyze "Movie.mkv"          # or a folder; --json for machine output
python skills/mkv-tracks/scripts/mkvtracks.py pack "Movie.mkv" \
  --drop 3,4 \
  --add "Movie.cz.srt::lang=cze;name=Czech;default" \
  --set "1::default=1" \
  --replace
```

| Option | Meaning |
|---|---|
| `--drop 3,4` / `--keep 0,1,2` | Remove these track IDs, or keep only these. The IDs are the ones `analyze` shows (mkvmerge IDs). |
| `--add "PATH::opts"` | Add an external subtitle. Options: `lang=`, `name=`, `default`, `forced`, `sdh`, `delay=<ms>`, `charset=` |
| `--set "ID::opts"` | Change a kept track: `lang=`, `name=`, `default=0/1`, `forced=0/1` |
| `--replace` | Write a temporary file, verify it, then swap it in place of the original |
| `-o out.mkv` | Write to a new file instead. Without `--replace` or `-o`, the output is `<name>.repack.mkv`. |
| `--delete-external` | Delete the added subtitle files after a successful pack |
| `--title`, `--no-attachments`, `--no-chapters` | Container-level options |
| `--dry-run` | Only print the mkvmerge command |

What it handles for you:

- **Language** of an added subtitle comes from the filename (`.cz.srt`, `.eng.srt`, …) or, failing that, from the text itself (Czech, Slovak, Polish, Hungarian, German, Russian/Ukrainian, English).
- **Charset**: legacy cp1250/cp1252 subtitles are converted to UTF-8 inside the MKV.
- **Default flags**: marking an added subtitle as `default` clears the default flag on the existing subtitle tracks.
- **Safety**: it checks free disk space before starting. If mkvmerge fails or the track count doesn't match, the original is left untouched.

## mkv-subtitle-translate

Ask: *"translate the subtitles of Movie.mkv to Czech and Slovak"*. The agent:

1. extracts the English text subtitle track (it skips forced and SDH tracks; PGS/VobSub need OCR first, so it asks for an .srt)
2. asks in the checklist UI for the languages (Czech, Slovak, German and Polish are always offered, anything else can be typed in), whether to keep or replace the English track, and which track should be default
3. **reads the whole film first** and writes a style sheet: characters and their gender (this matters for Slavic grammar), formal vs informal "you" for each pair of characters, names and terms, and how strong the swearing should be
4. translates in ~120-cue parts with context from the previous and next part. Sentences that run across cues are translated as a whole and split back. It condenses to subtitle norms (42 characters per line, 2 lines, ~17 characters per second), adapts idioms and jokes, and uses target-language typography
5. validates every part (no missing cues, balanced `<i>` tags, line length and reading speed, English left in by mistake), builds an SRT with the **original timings untouched**, and muxes it into the MKV

The workbench script runs on its own as well:

```bash
T=skills/mkv-subtitle-translate/scripts/subtrans.py
python $T extract Movie.mkv            # -> Movie.subtrans/ (source.txt, style.md, parts)
python $T show  Movie.subtrans --lang cze --part 1
python $T check Movie.subtrans --lang cze
python $T build Movie.subtrans --lang cze
python $T pack  Movie.subtrans --langs cze,slo --default cze --replace [--drop-english]
```

## License

MIT
