# mkv-tracks

An agent skill (Claude Code, Codex, and other agents that read `SKILL.md`) for MKV files. It:

- **analyzes** an `.mkv` file and lists its video, audio and subtitle tracks (codec, language, name, default/forced flags, channels, resolution), its chapters and attachments, and any external subtitle files sitting next to it, with their guessed language and encoding
- lets you **choose** which tracks to remove and which external subtitles to add, using the agent's checkbox UI
- **packs** the result with `mkvmerge`. Streams are copied, not re-encoded, so quality stays the same. The original file is replaced only after the output has been checked.

## Install

```bash
npx skills add depthark/mkv-tracks
```

Or clone the repo into your skills folder, for example `~/.agents/skills/mkv-tracks` or `~/.claude/skills/mkv-tracks`.

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
python scripts/mkvtracks.py analyze "Movie.mkv"          # or a folder; --json for machine output
python scripts/mkvtracks.py pack "Movie.mkv" \
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

## License

MIT
