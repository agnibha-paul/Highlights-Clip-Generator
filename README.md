# Highlight Clip Generator

Turn a long video (gameplay, football matches, podcasts, whatever) into short highlight clips — automatically, running 100% locally. Comes with both a GUI and a command-line mode.

## How it picks highlights

Two signals get combined into a score for every second of the video:

- **Volume spikes** — sudden loudness jumps in the audio (commentary getting excited, crowd noise, etc.)
- **Hype keywords** — the audio gets transcribed with Whisper, then scanned for hype words/phrases ("let's go", "no way", "goal", "clutch", etc. — fully editable, see below)

The highest-scoring, non-overlapping moments get picked and cut into clips with ffmpeg.

## Setup

**1. Install ffmpeg**

You need `ffmpeg.exe` **and** `ffprobe.exe` — they come together in the full build.

- Windows: download the "release full" build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/), extract it, and either:
  - add the `bin` folder (containing both exes) to your PATH, **or**
  - drop `ffmpeg.exe` and `ffprobe.exe` directly next to the script
- Mac: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

Verify it worked:
```bash
ffmpeg -version
ffprobe -version
```

**2. Install Python packages**

```bash
python -m pip install openai-whisper numpy
```

(A venv is optional but recommended if you don't want these installed globally — see [Optional: using a venv](#optional-using-a-venv) below.)

## Usage

### GUI mode
Just run the script with no arguments:
```bash
python autoclip_gui.py
```

This opens a window where you can:
- Browse for your input video
- Set number of clips, clip length, minimum gap between clips
- Pick a Whisper model size
- Toggle vertical (9:16) cropping for Shorts/TikTok/Reels
- Choose an output folder
- Hit **Run** and watch progress in the log box

### CLI mode
Pass a video path (and optional flags) to run headless, no window:
```bash
python autoclip_gui.py my_video.mp4 --clips 5 --length 25

# vertical output for Shorts/TikTok/Reels
python autoclip_gui.py my_video.mp4 --clips 5 --length 25 --vertical
```

| Flag | Default | Meaning |
|---|---|---|
| `--clips` | 4 | Number of clips to generate |
| `--length` | 25 | Target clip length in seconds |
| `--min-gap` | 45 | Minimum seconds between selected moments |
| `--model` | base | Whisper model: `tiny` (fastest) / `base` / `small` / `medium` (most accurate) |
| `--outdir` | clips | Output folder |
| `--vertical` | off | Crop clips to 9:16 vertical |

Output clips land in `clips/clip_01.mp4`, `clip_02.mp4`, etc. (or wherever `--outdir` / the GUI's output field points).

## Whisper model sizes

| Model | Download size | Speed | Notes |
|---|---|---|---|
| tiny | ~75 MB | fastest | good enough if you only care about volume spikes, less accurate on transcript |
| base | ~145 MB | fast | good default, balances speed and accuracy |
| small | ~485 MB | moderate | better for noisy audio (e.g. broadcast crowd noise) |
| medium | ~1.5 GB | slow | best accuracy, only worth it on a decent machine |

## Tuning tips

- **Wrong moments getting picked?** Edit the `HYPE_PATTERNS` list near the top of the script. Add words/phrases specific to your content (e.g. football: "goal", "penalty", "red card"; gaming slang specific to you or your friends).
- **Clips cut off the buildup?** Increase `--length`, or tweak the `pre`/`post` split inside `pick_top_windows()` (currently 35% before the peak, 65% after).
- **Broadcast/crowd audio has a high constant baseline?** The volume-spike detection compares each moment to a smoothed local baseline — if the whole video is loud, spikes are less pronounced. Try `--model small` for better keyword detection to compensate, and/or increase `--clips` and review results.
- **Whisper too slow?** Use `--model tiny` for much faster transcription (keyword detection gets a bit less accurate, but volume-spike detection is unaffected).

## About `--vertical`

Since source footage is usually 16:9, vertical mode scales the video up and center-crops the width down to a 1080x1920 frame. This works well when the action is centered on screen (true for most gameplay/sports footage). If your HUD/minimap/scoreboard sits near the edges and gets cropped out, a letterboxed (uncropped, blurred-background) style would work better instead — not currently built in, but easy to add.

Note: vertical clips require re-encoding (cropping can't be done with a stream copy), so they take noticeably longer to generate than the default mode.

## Optional: using a venv

If you don't want the Python packages installed globally:
```bash
python -m venv venv
venv\Scripts\activate      # Windows
source venv/bin/activate   # Mac/Linux
python -m pip install openai-whisper numpy
```
If using VS Code, select this venv as your interpreter (`Ctrl+Shift+P` → "Python: Select Interpreter") so it can resolve the imports correctly.

## What it does NOT do (yet)

- No auto-captions burned into clips (Whisper already gives you the transcript with timestamps if you want to add this yourself)
- No letterboxed vertical mode (it makes vertical clips via center-crop with `--vertical`, but doesn't offer the alternate style of keeping the full frame uncropped with blurred/solid bars filling the rest)
- No visual highlight detection (goals/kills detected purely from audio + commentary, not from the video frames)

## Troubleshooting

- **`ffprobe` errors or "command not found"** — make sure both `ffmpeg.exe` and `ffprobe.exe` are on PATH (or next to the script), and that you opened a *new* terminal after adding to PATH.
- **`Package(s) not found` from pip** — you're likely checking/installing in a different Python environment than the one actually running the script. Confirm with `python -c "import sys; print(sys.executable)"`.
- **Pylance "import could not be resolved" in VS Code** — this is just the editor pointing at the wrong interpreter; select the correct one via `Ctrl+Shift+P` → "Python: Select Interpreter", then reload the window.
