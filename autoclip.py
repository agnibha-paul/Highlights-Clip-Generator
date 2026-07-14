import argparse
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

# ---- Core processing logic -----

HYPE_PATTERNS = [
    r"\bno way\b", r"\blet'?s go\b", r"\boh my god\b", r"\bomg\b",
    r"\bwhat\b!*", r"\bholy shit\b", r"\binsane\b", r"\bcrazy\b",
    r"\bclutch\b", r"\bwhat the hell\b", r"\byes+\b", r"\bwow\b",
    r"\bdid you see\b", r"\bnice\b!+", r"\bgg\b", r"\bunbelievable\b",
    r"\bhaha", r"\bno way\b", r"\bshut up\b", r"\bare you kidding\b", r"\bgoal+\b", r"\bscores?\b", r"\bwhat a strike\b", r"\bpenalty\b",
    r"\bred card\b", r"\byellow card\b", r"\bvar\b", r"\boffside\b",
    r"\bsave\b", r"\bunbelievable save\b", r"\bhe's done it\b",
    r"\bwhat a goal\b", r"\bhe scores\b"
]
HYPE_RE = re.compile("|".join(HYPE_PATTERNS), re.IGNORECASE)


def run(cmd, log=print):
    log(f"$ {' '.join(cmd)}\n")
    subprocess.run(cmd, check=True)


def extract_audio(video_path, audio_path, log=print):
    run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", audio_path
    ], log=log)


def get_video_duration(video_path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],     # <- this line
        capture_output=True, text=True, check=True
    )
    return float(out.stdout.strip())


def compute_volume_curve(audio_path, window_sec=1.0):
    """Return (timestamps, energy) using per-window RMS."""
    import wave
    with wave.open(audio_path, "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

    win = int(window_sec * sr)
    n_windows = max(1, len(data) // win)
    energy = np.zeros(n_windows)
    for i in range(n_windows):
        chunk = data[i * win:(i + 1) * win]
        energy[i] = np.sqrt(np.mean(chunk ** 2) + 1e-9)

    timestamps = np.arange(n_windows) * window_sec
    return timestamps, energy


def transcribe(audio_path, model_name, log=print):
    import whisper
    log(f"Loading Whisper model '{model_name}'...\n")
    model = whisper.load_model(model_name)
    log("Transcribing (this can take a while for longer videos)...\n")
    result = model.transcribe(audio_path, verbose=False)
    return result["segments"]  # list of {start, end, text}


def score_timeline(duration, timestamps, energy, segments, window_sec=1.0):
    """Combine volume spikes + keyword hits into one score per timestamp bucket."""
    scores = np.zeros_like(energy)

    baseline = np.convolve(energy, np.ones(20) / 20, mode="same")
    spike = np.clip(energy - baseline, 0, None)
    if spike.max() > 0:
        spike = spike / spike.max()
    scores += spike * 1.0

    for seg in segments:
        if HYPE_RE.search(seg["text"]):
            mid = (seg["start"] + seg["end"]) / 2
            idx = int(mid / window_sec)
            if 0 <= idx < len(scores):
                for d in range(-1, 2):
                    if 0 <= idx + d < len(scores):
                        scores[idx + d] += 0.8

    return scores


def pick_top_windows(timestamps, scores, duration, n_clips, clip_len, min_gap):
    """Greedily pick top-scoring centers, spaced apart, and build clip windows."""
    order = np.argsort(scores)[::-1]
    chosen = []
    for idx in order:
        t = timestamps[idx]
        if any(abs(t - c) < min_gap for c in chosen):
            continue
        chosen.append(t)
        if len(chosen) >= n_clips:
            break

    windows = []
    pre = clip_len * 0.35
    post = clip_len * 0.65
    for c in sorted(chosen):
        start = max(0, c - pre)
        end = min(duration, c + post)
        if end - start < clip_len:
            if start == 0:
                end = min(duration, start + clip_len)
            elif end == duration:
                start = max(0, end - clip_len)
        windows.append((start, end))
    return windows


def cut_clip(video_path, start, end, out_path, vertical=False, log=print):
    if not vertical:
        run([
            "ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
            "-i", video_path, "-c", "copy", out_path
        ], log=log)
        return

    # Vertical (9:16) for YT Shorts / Reels: scale up so height
    vf = (
        "scale=-2:1920,"
        "crop=1080:1920:(iw-1080)/2:0"
    )
    run([
        "ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        out_path
    ], log=log)


def generate_clips(video, clips, length, min_gap, model, outdir, vertical, log=print):
    """Runs the full pipeline. `log` is called with each status line."""
    if not os.path.exists(video):
        log(f"File not found: {video}\n")
        return False

    os.makedirs(outdir, exist_ok=True)
    audio_path = "_autoclip_audio.wav"

    log("Step 1/4: extracting audio...\n")
    extract_audio(video, audio_path, log=log)

    duration = get_video_duration(video)
    log(f"Video duration: {duration:.1f}s\n")

    log("Step 2/4: computing volume curve...\n")
    timestamps, energy = compute_volume_curve(audio_path)

    log("Step 3/4: transcribing with Whisper...\n")
    segments = transcribe(audio_path, model, log=log)

    log("Step 4/4: scoring and selecting highlight windows...\n")
    scores = score_timeline(duration, timestamps, energy, segments)
    windows = pick_top_windows(timestamps, scores, duration, clips, length, min_gap)

    if not windows:
        log("No windows found — video may be too short or too quiet.\n")
        if os.path.exists(audio_path):
            os.remove(audio_path)
        return False

    log(f"\nSelected {len(windows)} clip window(s):\n")
    for i, (s, e) in enumerate(windows, 1):
        log(f"  Clip {i}: {s:.1f}s -> {e:.1f}s ({e - s:.1f}s)\n")

    for i, (s, e) in enumerate(windows, 1):
        out_path = os.path.join(outdir, f"clip_{i:02d}.mp4")
        cut_clip(video, s, e, out_path, vertical=vertical, log=log)
        log(f"Wrote {out_path}\n")

    os.remove(audio_path)
    log(f"\nDone! Clips are in: {outdir}\n")
    return True

# ------------------------------- Tkinter GUI --------------------------------

class AutoClipGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Highlight Clip Generator")
        self.geometry("640x560")
        self.resizable(True, True)

        self.video_path = tk.StringVar()
        self.outdir = tk.StringVar(value="clips")
        self.clips = tk.IntVar(value=4)
        self.length = tk.DoubleVar(value=25.0)
        self.min_gap = tk.DoubleVar(value=45.0)
        self.model = tk.StringVar(value="base")
        self.vertical = tk.BooleanVar(value=False)

        self._build_widgets()

    # ------------------------------------ UI ----------------------------
    def _build_widgets(self):
        pad = {"padx": 10, "pady": 6}

        file_frame = ttk.LabelFrame(self, text="Input video")
        file_frame.pack(fill="x", **pad)

        ttk.Entry(file_frame, textvariable=self.video_path).pack(
            side="left", fill="x", expand=True, padx=(10, 6), pady=10)
        ttk.Button(file_frame, text="Browse...", command=self.browse_file).pack(
            side="left", padx=(0, 10), pady=10)

        opts_frame = ttk.LabelFrame(self, text="Options")
        opts_frame.pack(fill="x", **pad)

        row = 0
        ttk.Label(opts_frame, text="Number of clips:").grid(row=row, column=0, sticky="w", padx=10, pady=6)
        ttk.Spinbox(opts_frame, from_=1, to=50, textvariable=self.clips, width=8).grid(
            row=row, column=1, sticky="w", pady=6)

        ttk.Label(opts_frame, text="Clip length (sec):").grid(row=row, column=2, sticky="w", padx=10, pady=6)
        ttk.Spinbox(opts_frame, from_=1, to=600, increment=1, textvariable=self.length, width=8).grid(
            row=row, column=3, sticky="w", pady=6)

        row += 1
        ttk.Label(opts_frame, text="Min gap between clips (sec):").grid(
            row=row, column=0, sticky="w", padx=10, pady=6)
        ttk.Spinbox(opts_frame, from_=0, to=600, increment=1, textvariable=self.min_gap, width=8).grid(
            row=row, column=1, sticky="w", pady=6)

        ttk.Label(opts_frame, text="Whisper model:").grid(row=row, column=2, sticky="w", padx=10, pady=6)
        ttk.Combobox(opts_frame, textvariable=self.model, width=10, state="readonly",
                     values=["tiny", "base", "small", "medium"]).grid(row=row, column=3, sticky="w", pady=6)

        row += 1
        ttk.Checkbutton(opts_frame, text="Vertical (9:16 for Shorts/TikTok/Reels)",
                        variable=self.vertical).grid(row=row, column=0, columnspan=2, sticky="w", padx=10, pady=6)

        out_frame = ttk.LabelFrame(self, text="Output folder")
        out_frame.pack(fill="x", **pad)

        ttk.Entry(out_frame, textvariable=self.outdir).pack(
            side="left", fill="x", expand=True, padx=(10, 6), pady=10)
        ttk.Button(out_frame, text="Choose...", command=self.browse_outdir).pack(
            side="left", padx=(0, 10), pady=10)

        run_frame = ttk.Frame(self)
        run_frame.pack(fill="x", **pad)

        self.run_btn = ttk.Button(run_frame, text="Run", command=self.run_clicked)
        self.run_btn.pack(side="left")

        self.status_label = ttk.Label(run_frame, text="Idle")
        self.status_label.pack(side="left", padx=10)

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)

        self.log_text = tk.Text(log_frame, wrap="word", height=15, state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y", pady=10, padx=(0, 10))
        self.log_text.configure(yscrollcommand=scrollbar.set)

    # --------------------------- actions ---------------------------------
    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Select a video file",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.video_path.set(path)

    def browse_outdir(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.outdir.set(path)

    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def run_clicked(self):
        video = self.video_path.get().strip()
        if not video:
            messagebox.showerror("No file selected", "Please choose a video file first.")
            return
        if not os.path.exists(video):
            messagebox.showerror("File not found", f"Couldn't find:\n{video}")
            return

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.run_btn.configure(state="disabled")
        self.status_label.configure(text="Running...")

        args = (
            video,
            self.clips.get(),
            self.length.get(),
            self.min_gap.get(),
            self.model.get(),
            self.outdir.get() or "clips",
            self.vertical.get(),
        )
        thread = threading.Thread(target=self._run_pipeline, args=args, daemon=True)
        thread.start()

    def _run_pipeline(self, video, clips, length, min_gap, model, outdir, vertical):
        def thread_safe_log(text):
            self.after(0, self.log, text)

        try:
            success = generate_clips(
                video, clips, length, min_gap, model, outdir, vertical,
                log=thread_safe_log,
            )
        except Exception as e:
            thread_safe_log(f"\nError: {e}\n")
            success = False

        def finish():
            self.run_btn.configure(state="normal")
            if success:
                self.status_label.configure(text="Done")
                messagebox.showinfo("Finished", f"Clips written to:\n{outdir}")
            else:
                self.status_label.configure(text="Failed")
                messagebox.showerror("Error", "Something went wrong. Check the log for details.")

        self.after(0, finish)


# --------------------------------- Entry point ------------------------------

def main():
    if len(sys.argv) > 1:
        ap = argparse.ArgumentParser(description="Auto-generate highlight clips from a video.")
        ap.add_argument("video", help="Path to input video file")
        ap.add_argument("--clips", type=int, default=4, help="Number of clips to produce")
        ap.add_argument("--length", type=float, default=25, help="Target clip length in seconds")
        ap.add_argument("--min-gap", type=float, default=45, help="Minimum seconds between clip centers")
        ap.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium"],
                         help="Whisper model size (bigger = more accurate, slower)")
        ap.add_argument("--outdir", default="clips", help="Output directory for clips")
        ap.add_argument("--vertical", action="store_true",
                         help="Crop clips to 9:16 vertical (YouTube Shorts / TikTok / Reels)")
        args = ap.parse_args()

        ok = generate_clips(
            args.video, args.clips, args.length, args.min_gap,
            args.model, args.outdir, args.vertical,
        )
        sys.exit(0 if ok else 1)
    else:
        app = AutoClipGUI()
        app.mainloop()


if __name__ == "__main__":
    main()
