# ClipForge

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Platform: Windows | macOS | Linux](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Runs 100% locally](https://img.shields.io/badge/Runs-100%25%20locally-orange)

**A free, fully local auto-clipper for long videos.**
Give ClipForge a video file and it finds the highest-energy moments, cuts
vertical 9:16 clips around them, and edits each clip the way a human editor
would: pop-style word captions, a hook title card, a gentle push-in zoom, a
progress bar, and loudness-normalised audio. No accounts, no paid APIs, no
cloud uploads, and no posting. Everything runs on your own machine.

Built for **authorised clipping**: your own videos, or content you hold a
campaign, licence, or permission to clip.

---

## Contents

- [Why ClipForge](#why-clipforge)
- [What a finished clip looks like](#what-a-finished-clip-looks-like)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Usage](#usage)
- [All flags](#all-flags)
- [Campaign mode](#campaign-mode)
- [Batch mode](#batch-mode)
- [Project structure](#project-structure)
- [Honest limits](#honest-limits)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Author](#author)

## Why ClipForge

- **100% local and free.** Speech recognition, editing, and rendering all
  happen on your machine with open-source tools. There is nothing to sign
  up for and nothing per clip to pay.
- **Edited, not just cut.** Clips end on complete sentences, captions are
  timed from the clip's own audio, and the spoken word pops as it is
  spoken. The output is made to be posted, not re-edited.
- **Campaign mode.** Paid clipping campaigns each demand their own clip
  format. ClipForge stores one small JSON profile per campaign and applies
  its length limits, watermark, name card, and post text automatically,
  with a ready-to-copy post pack for every clip.
- **No posting, ever.** The tool has no posting, submitting, or uploading
  code of any kind. You review every clip and post it yourself, from your
  own device. This is deliberate: accounts stay in their owner's hands.

## What a finished clip looks like

- Vertical **720x1280 (9:16)** MP4, ready for Reels, Shorts, and TikTok.
- **Pop captions** in a heavy display font (Anton, bundled): short phrases
  of up to 3 words, earlier words in white, the word being spoken turns
  yellow and swells for a beat. Common swear words are masked in the
  captions; the audio itself is untouched.
- A **hook title card** across the first ~3 seconds, built from the words
  around the clip's peak moment (or set exactly with `--hook`).
- A slow **push-in zoom** (about 1.00x to 1.07x) so still footage feels
  alive, and a thin **yellow progress bar** filling along the bottom.
- Audio **loudness-normalised to about -14 LUFS**, the usual
  Shorts/Reels target.
- A small **JSON sidecar** per clip (source, start, end, score, hook text)
  and, in campaign mode, a **`.post.txt` post pack**: suggested title,
  exact post text, tags, and the campaign's posting reminders.

## How it works

1. **Finds moments from sound.** Audio loudness is measured second by
   second, smoothed, and scored by level and by how sharply it rises
   (laughter, shouting, a punchline landing). Near-silent stretches are
   marked down.
2. **Cuts on complete thoughts.** Each clip starts a few seconds before
   its peak, then both cut points are snapped to sentence boundaries, so
   clips never end mid-sentence. Clip lengths vary a little around the
   requested length. `--no-snap` cuts at the exact requested times.
3. **Transcribes locally.** Speech is transcribed with faster-whisper
   using word-level timing, and captions are generated from the clip's
   own audio, never from a plan or a script.
4. **Edits and renders.** Captions, hook card, zoom, progress bar, layout
   (full frame over a blurred background, or centre-crop fill), any
   campaign watermark or name card, and loudness normalisation are applied
   in one render per clip.
5. **Writes files, never posts.** MP4 + JSON (+ post pack in campaign
   mode) land in the output folder. Reviewing and posting stay with you.

## Requirements

- Windows, macOS, or Linux with Python **3.10+**
- **ffmpeg** with libass installed system-wide (the standard build for
  each platform includes it; the Windows note below names the right one)
- About **2-4 GB** of disk for the tool and the speech model, and
  **15-20 GB** free if you keep raw recordings around
- Internet once, on the first run, to download the speech model
  (about 500 MB). After that it works fully offline.

## Install

### Ubuntu / Linux

```bash
sudo apt update && sudo apt install -y ffmpeg python3-venv
./install.sh
```

The installer creates a `.venv`, installs `faster-whisper` and `numpy`,
and checks ffmpeg.

### macOS

Install ffmpeg and Python with Homebrew, then set up the same way:

```bash
brew install ffmpeg python
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
```

(`./install.sh` also works on macOS once ffmpeg is installed; it only
uses python3, pip, and an ffmpeg check.)

### Windows

Use PowerShell. Install Python and the full ffmpeg build (it includes
libass, which the burned-in captions need), then set up the environment:

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Finished clips are small (about 4-30 MB each). The tool only reads the
files you give it; it never keeps or uploads your footage.

## Quick start

```bash
./.venv/bin/python clipper.py --file episode.mp4 --top 3
```

On Windows, use `.\.venv\Scripts\python.exe` in place of
`./.venv/bin/python` in every command in this README.

Three finished clips, with JSON sidecars, appear in `output/`.

## Usage

```bash
# More clips, custom length and lead-in
./.venv/bin/python clipper.py --file episode.mp4 --top 5 --clip-len 30 --lead-in 6

# Centre-crop fill instead of full frame over blur
./.venv/bin/python clipper.py --file gameplay.mp4 --top 4 --layout fill

# Captions in a specific language
./.venv/bin/python clipper.py --file hindi.mp4 --top 3 --language hi

# Campaign mode: one profile applies the campaign's whole format
./.venv/bin/python clipper.py --file episode.mp4 --campaign example-campaign

# Re-render one clip with an exact, human-approved hook title
./.venv/bin/python clipper.py --file episode.mp4 --top 1 --hook "AND THAT'S WHY I GOT FIRED"

# Batch mode: a folder of campaign subfolders, processed in one run
./.venv/bin/python clipper.py --batch-dir incoming/ --top 3
```

## All flags

| Flag | Default | Meaning |
|---|---|---|
| `--file` | (single mode) | Input video file |
| `--batch-dir` | off | Batch mode over campaign subfolders (see below) |
| `--top` | 3 | How many clips to make per file |
| `--clip-len` | 30 / profile | Clip length in seconds (clamped to the profile's min/max) |
| `--lead-in` | 6 / profile | Seconds before the moment peak |
| `--layout` | fit / profile | `fit` = full frame over a blurred background; `fill` = centre crop |
| `--output-dir` | output | Where clips are written |
| `--language` | auto / profile | Caption language, e.g. `en`, `hi` |
| `--min-gap` | 60 / profile | Minimum seconds between picked moments |
| `--model` | small | Whisper model size (`base`, `small`, ...) |
| `--campaign` | off | Campaign profile from `profiles/` (single-file mode) |
| `--name-card` | profile/folder | Guest name shown on the campaign name card |
| `--hook` | auto | Force this exact text as the hook title card, for single-clip re-renders with human-approved wording |
| `--no-hook` | off | Skip the hook title card |
| `--no-pop` | off | Skip the scale pop on the spoken word |
| `--no-zoom` | off | Skip the gentle push-in zoom |
| `--no-progress` | off | Skip the yellow progress bar |
| `--no-snap` | off | Cut at the exact requested times, no sentence snapping |

Command-line flags always win over profile values; profile values win over
the built-in defaults.

## Campaign mode

Paid clipping campaigns each have their own rules: allowed footage,
clip length, format, caption wording, and posting steps. ClipForge keeps those rules in one JSON file
per campaign, so a new campaign is a settings file, never a code change.

- **`profiles/`** holds the settings the tool actually applies: clip
  length limits, layout, watermark file and placement, name card, hook
  and caption switches, and the post text template.
- Real campaign profiles are **kept private by design**: they contain
  campaign owners' briefs, pay terms, and footage links, which are not
  ours to publish. This repo ships a template profile instead.
- Every campaign clip gets a **post pack** (`<clip>.post.txt`): a
  suggested title from the hook, the exact post text to copy, tags, and
  the campaign's reminders (post and submit back to back, keep the post
  up, no boosts).

Profile included:

- **`example-campaign`**: a template with neutral placeholder values
  (clips 15-60 s, 9:16, no watermark, no name card). Copy it, rename it,
  and fill in a real campaign's rules before use. Profiles can also set
  a watermark image (drawn for the whole clip) and a guest name card
  (shown from the first frame, text from `--name-card` or a batch
  folder's `name_card.txt`).

Adding a campaign: copy an existing JSON in `profiles/`, edit the fields
(see `profiles/README.md` for the full field list), done. Read the new
campaign's rules off its own page first; never guess another campaign's
rules, and re-check a live campaign's status and budget before clipping,
because campaigns close.

## Batch mode

```bash
./.venv/bin/python clipper.py --batch-dir incoming/ --top 3
```

Each subfolder of the batch folder is named after a campaign profile, and
every video inside it is processed with that profile:

```
incoming/
  example-campaign/
    episode-part-08.mp4
  talk-show/
    name_card.txt        (first line = guest name for the folder)
    interview.mp4
  output/                (created by the run)
    batch-summary.json
    example-campaign/episode-part-08/clip-01.mp4 + clip-01.post.txt + ...
```

The speech model loads once for the whole batch. One bad file never kills
the run: it is logged in `batch-summary.json` and processing continues.

## Project structure

```
ClipForge/
  clipper.py            the tool (single file, no package to install)
  install.sh            one-time installer (Ubuntu / Linux / macOS)
  requirements.txt      faster-whisper + numpy
  fonts/                Anton + Montserrat ExtraBold (caption fonts)
  profiles/             campaign settings the tool applies
    assets/             watermark files used by profiles
  output/               finished clips land here (created per run)
```

## Honest limits

- Moment picking uses **sound energy only**. A loud moment is not always
  an interesting moment: a door slam can outscore a quiet, brilliant line.
  Review every clip before using it.
- It works on **files only**. No live-stream capture and no chat reading.
- Caption quality depends on the audio: clear speech captions well; heavy
  music or overlapping voices caption poorly.
- Sentence snapping trusts the transcription's word timing and
  punctuation. On mumbled, punctuation-free speech the nearest boundary
  may be a pause rather than a true sentence end, and clip lengths vary
  by a few seconds around the requested length.
- The auto-built hook title is a machine's choice from the words around
  the peak. It is usually a real line from the clip, but glance at it in
  the clip's JSON before posting, or set it exactly with `--hook`.
- Loudness normalisation is a single pass: integrated loudness lands on
  target (about -14 LUFS), but the true peak is not hard-limited.
- A profile cannot check that the right person is speaking in a clip, or
  that a campaign is still live. Those stay human checks, like every clip
  this tool makes.

## Roadmap

- More campaign profiles, added the same way: read the rules, write the
  JSON, test on one file.
- Better moment picking that also weighs speech content, not just sound.
- Out of scope by design: posting, submitting, and account automation.
  ClipForge makes files; people post them.

## Contributing

Issues and pull requests are welcome. Please keep the project's two
standing rules: everything runs locally, and the tool never posts,
submits, or uploads on anyone's behalf.

## License

MIT. Free to use, copy, and change. See `LICENSE`.

## Author

Built by **Jay Patel** ([@JayPatelCode](https://github.com/JayPatelCode)).
ClipForge grew out of real campaign work: every feature in it exists
because a real clip needed it.
