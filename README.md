# compress-mov

Batch-compress `.mov` files to HEVC `.mp4` with ffmpeg. Run it from a terminal, or wire it up as a
Finder Quick Action so you can right-click videos and compress them without opening anything.

For each `Clip.mov` it writes `Clip (HEVC).mp4` next to the original. **The original is never
modified or deleted.**

## Requirements

- macOS (the Quick Action, Terminal relaunch, notifications and sleep-prevention are macOS-only; the
  encoder itself only needs ffmpeg)
- Python 3.11+
- ffmpeg **with libx265**, which includes ffprobe: `brew install ffmpeg`

ffmpeg and ffprobe are found on `$PATH`, then in `/opt/homebrew/bin` and `/usr/local/bin`, so both
Apple Silicon and Intel Homebrew installs work, including from a Quick Action's minimal `$PATH`.

## Install

```sh
uv tool install git+https://github.com/maxludden/compress-mov
# or, from a checkout:
uv tool install .
```

This puts `compress-mov` in `~/.local/bin`.

## Usage

```sh
compress-mov clip.mov                 # one file
compress-mov ~/Movies/trip            # every .mov directly inside a folder
compress-mov -r ~/Movies              # ...and in all subfolders
compress-mov a.mov b.mov ~/Movies/x   # any mix of files and folders
```

| Option          | Effect                                                              |
| --------------- | ------------------------------------------------------------------- |
| `-r`, `--recursive` | Include `.mov` files in subdirectories of each folder           |
| `--keep-larger` | Keep the `.mp4` even when it isn't smaller than the original        |

Matching is case-insensitive (`.mov`, `.MOV`). Hidden files and folders are skipped, which includes the
`._*.mov` AppleDouble sidecars macOS writes on exFAT/SMB drives. Each file is encoded once even if it
is passed twice, or via both a folder and a symlink.

**Exit codes:** `0` everything succeeded (or was skipped for no savings), `1` at least one file failed
or no `.mov` files were found, `2` usage error, `130` interrupted.

## Finder Quick Action

1. Open **Automator**, choose **New Document → Quick Action**.
2. Set *Workflow receives current* **files or folders** in **Finder**.
3. Add a **Run Shell Script** action with *Shell* `/bin/zsh` and *Pass input* **as arguments**:

   ```sh
   "$HOME/.local/bin/compress-mov" "$@"
   ```

4. Save it (for example as "Compress to HEVC"). It now appears under **Right-click → Quick Actions**.

A Quick Action has no terminal, so when compress-mov sees it's running headless (macOS, and none of
stdin/stdout/stderr is a terminal) it reopens itself in a Terminal window, shows progress there, and
posts a notification banner when finished. Set `COMPRESS_MOV_TERMINAL=iTerm` to use a different
terminal app, or `COMPRESS_MOV_NO_RELAUNCH=1` to disable the relaunch (it is also skipped over ssh).

## Terminal UI (optional)

An interactive front end, built on [Textual](https://textual.textualize.io), for queueing files and watching the
encode. It is an optional extra with its own command, so the plain CLI stays lightweight and unchanged:

```sh
uv tool install 'compress-mov[tui]'     # or: pip install 'compress-mov[tui]'
compress-mov-tui                         # empty queue
compress-mov-tui -r ~/Movies/trip        # queue files/folders on startup (same -r / --keep-larger options)
```

| Key | Action                                                                 |
| --- | ---------------------------------------------------------------------- |
| `a` | Focus the path box: type or paste a file/folder (drag a file onto the terminal to paste its path), then Enter |
| `s` | Start encoding the queue                                               |
| `x` | Clear the queue (not while encoding)                                   |
| `q` | Quit. If an encode is running, ffmpeg is stopped and its partial file removed |

The **Recursive** and **Keep larger** switches match `-r` and `--keep-larger`. The queue shows each file's size,
status (`queued`, `encoding`, `done`, `skipped`, `failed`, `cancelled`) and result; warnings and the final
summary appear in the log pane. It uses the same encoder, output naming and log file as the CLI.

Without the extra installed, `compress-mov-tui` prints how to install it and exits with status 1; `compress-mov`
never imports Textual, so it is unaffected either way. The Finder Quick Action keeps using the CLI.

## What gets encoded

- **Video:** libx265, `-preset slow -crf 28`, tagged `hvc1` so QuickTime and Apple devices play it.
  Every video stream is encoded.
- **HDR / 10-bit:** 10-bit sources and HDR (HLG, PQ) sources are encoded as 10-bit
  (`yuv420p10le`), and the source's colour primaries, transfer, matrix and range are carried over.
  Everything else is 8-bit `yuv420p`. Dolby Vision enhancement data is not preserved (the HDR base layer is).
- **Audio:** every audio track, re-encoded to AAC at 64 kbps per channel (128 kbps minimum, so 5.1 gets 384 kbps).
- **Subtitles:** text subtitles are converted to `mov_text`. Bitmap subtitles are copied if the container accepts them.
- **Timecode:** preserved.
- **Not preserved:** alpha channels (HEVC `.mp4` can't hold them; you get a warning), and camera
  metadata tracks such as iPhone `mebx` data, which `.mp4` can't store. Best-effort streams (cover art,
  bitmap subtitles, data tracks) are tried first; if ffmpeg rejects them the file is retried without them and
  what was lost is reported.
- **Timestamps:** the output gets the original's modification time.
- **Not smaller?** If re-encoding doesn't shrink a file, the new `.mp4` is deleted and the file is
  reported as skipped. Pass `--keep-larger` to keep it anyway.
- **Name collisions:** an existing `Clip (HEVC).mp4` is never overwritten; the next run writes
  `Clip (HEVC) 2.mp4`, then `Clip (HEVC) 3.mp4`, and so on.
- **Symlinks:** the output goes next to the symlink, not next to the file it points to.

One bad file never stops the batch: it's reported as failed, its partial output is removed, and the
rest carry on. Ctrl-C (or SIGTERM) stops ffmpeg and removes the file being written.

## Log

Every run appends to `~/.config/logs/compress.log`: start/finish lines with sizes and timings, warnings,
retries, and ffmpeg's stderr for any failure. Logging is best-effort and never aborts a run.

## Development

```sh
uv sync                # includes Textual (dev group), so the TUI tests run too
uv run pytest          # the suite mocks ffmpeg, so it doesn't need it installed
uv run ruff check . && uv run ruff format --check .
uv run mypy            # strict, on src/
```

CI runs the same checks (`.github/workflows/ci.yml`) on Linux and macOS.

| Module          | Responsibility                                                     |
| --------------- | ------------------------------------------------------------------ |
| `cli.py`        | Argument parsing, batch loop, summary, exit codes                  |
| `discovery.py`  | Expanding files/folders into a de-duplicated list of `.mov` files  |
| `streams.py`    | Deciding what to keep: mapping, pixel format, colour tags, audio   |
| `encode.py`     | ffprobe/ffmpeg runs, progress, retries, cleanup, signal handling   |
| `terminal.py`   | Relaunching into Terminal for Quick Action use                     |
| `tui/`          | Optional Textual front end (`compress-mov-tui`); only `tui/app.py` imports Textual |
| `bins.py`       | Locating ffmpeg/ffprobe and the macOS helper binaries              |
| `logs.py`, `notify.py`, `power.py`, `formatting.py`, `ui.py` | Log file, banners, sleep prevention, display helpers |
