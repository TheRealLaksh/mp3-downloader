import subprocess
import sys
import json
import re
import threading
import time
import math
import os
import signal
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageFilter, ImageDraw


PLAYLIST_URL = "https://music.youtube.com/playlist?list=PLdcNZLpAI8easW7k-5luDy3j4LauXpbx-&si=aV6ovGfbLpF4tCw-"


try:
    BASE_DIR = Path(r"F:\walkman")
    BASE_DIR.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    BASE_DIR = Path.home() / "Music" / "walkman"
    BASE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DIR = BASE_DIR / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


LOW_END_MODE = False
BLUR_RADIUS = 40
ENABLE_TRANSPARENCY = not LOW_END_MODE


STAGE_ICONS = {
    "song": "🎵",
    "start": "⏳", "info": "⏳", "download": "⬇️", "merge": "🔄",
    "final": "✔", "done": "✔", "error": "❌", "retry": "🔁", "warn": "⚠"
}

STATUS_TEXT = {
    "start": "Downloading", "info": None, "download": "Downloading",
    "merge": "Merging", "final": "Finished", "done": "Finished",
    "error": "Error", "retry": "Retrying", "warn": "Warning"
}

SIZE_MULT = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}

def clean_name(name):
    if not name:
        return "Unknown"
   
    name = str(name)
    name = name.replace("#", "")
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    return name.strip().rstrip(".")

def format_duration(seconds):
    if seconds is None or seconds <= 0 or not math.isfinite(seconds):
        return "—"
    try:
        seconds = int(seconds)
    except ValueError:
        return "—"
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hrs: parts.append(f"{hrs} hr{'s' if hrs != 1 else ''}")
    if mins: parts.append(f"{mins} min{'s' if mins != 1 else ''}")
    if secs or not parts: parts.append(f"{secs} sec{'s' if secs != 1 else ''}")
    return " ".join(parts)

def get_playlist_info(url):

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--flat-playlist", url],
            capture_output=True, text=True, encoding='utf-8', errors="replace",
            startupinfo=startupinfo
        )
    except FileNotFoundError:
        raise RuntimeError("Python or yt-dlp module not found.")

    if proc.returncode != 0 or not proc.stdout.strip():
    
        err = proc.stderr or "yt-dlp failed to fetch info"
        raise RuntimeError(err.strip())

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("Failed to parse playlist JSON.")

    entries = data.get("entries") or []
   
    pl_title = clean_name(data.get("title", "Unknown Playlist"))
    song_titles = [clean_name(e.get("title")) for e in entries if e and e.get("title")]
    return pl_title, song_titles

def download_song(song, playlist_dir, progress_cb, stage_cb, stop_check_cb=None):
    safe_song = re.escape(song)
 
    cmd = [
        sys.executable, "-m", "yt_dlp", PLAYLIST_URL,
        "--newline",
        "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "0",
        "--match-title", safe_song,
        "-o", str(playlist_dir / "%(title)s.%(ext)s"),
        "--encoding", "utf-8"
    ]

  
    stage_cb("Starting", "start")
    stage_cb("Fetching song details", "info")
    stage_cb("Fetching artist details", "info")
    stage_cb("Fetching album details", "info")
    
    
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors="replace", bufsize=1,
            startupinfo=startupinfo
        )
    except Exception as e:
        stage_cb(f"Launch error: {e}", "error")
        return False

    stage_cb("Downloading banner", "download")

    total_bytes = None
    last_pct = None
    speed_samples = []
    saw_progress = False
    phase = "banner"

    try:
        while True:
          
            if stop_check_cb and stop_check_cb():
                proc.terminate()
                return False

            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue

           
            if "has already been downloaded" in line:
                saw_progress = True
                progress_cb(song, 100.0, 0, 0, 0, 0)
                stage_cb("Already downloaded", "final")
                continue

            
            size = re.search(r'of\s+~?([\d.]+)\s*(KiB|MiB|GiB)', line)
            done = re.search(r'(\d+(?:\.\d+)?)%', line)
            speed = re.search(r'at\s+([\d\.]+)\s*(KiB|MiB|GiB)/s', line)

            if size:
                try:
                    total_bytes = float(size.group(1)) * SIZE_MULT.get(size.group(2), 1024**2)
                except ValueError:
                    pass

            if done:
                saw_progress = True
                try:
                    pct = float(done.group(1))
                except ValueError:
                    pct = 0.0
                
                if pct > 100: pct = 100

               
                downloaded = total_bytes * (pct / 100) if total_bytes else 0
                remaining = total_bytes - downloaded if total_bytes else None

              
                if pct > 2 and phase == "banner":
                    stage_cb("Downloading song", "download")
                    phase = "song"

                if pct > 98 and phase == "song":
                    stage_cb("Merging everything", "merge")
                    phase = "merge"

                if speed:
                    try:
                        spd = float(speed.group(1)) * SIZE_MULT.get(speed.group(2), 1024**2)
                        speed_samples.append(spd)
                        if len(speed_samples) > 10: speed_samples.pop(0)
                        avg_speed = sum(speed_samples) / len(speed_samples)
                    except ValueError:
                        avg_speed = 0
                else:
                    avg_speed = 0

               
                eta = (remaining / avg_speed) if (avg_speed and avg_speed > 0 and remaining) else 0

                
                if pct != last_pct:
                    last_pct = pct
                    progress_cb(song, pct, downloaded, total_bytes, avg_speed, eta)

    except (OSError, ValueError):
        
        pass
    finally:
        # Fix: Ensure process is cleaned up
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    ret = proc.returncode
  
    if ret == 0 and saw_progress:
        stage_cb("Finalizing", "final")
        return True
    
    stage_cb("Download failed or interrupted", "error")
    return False

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Playlist Downloader")
        self.geometry("780x580")
        self.configure(bg="#0e0e0e")
        
      
        self.is_running = True
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        if ENABLE_TRANSPARENCY:
            try:
                self.attributes("-alpha", 0.97)
            except tk.TclError:
                pass 
                
        self.resizable(False, False)

        self.song_times = []
        self.failed_songs = []
        self.current_icon = None
        self.icon_pulse = False
        self.pulse_running = False
        self.pulse_tag = None
        
        self.target_playlist_pct = 0.0
        self.current_playlist_pct = 0.0
        self.target_song_pct = 0.0
        self.current_song_pct = 0.0

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Card.TFrame", background="#1a1a1a")
        style.configure("Header.TLabel", background="#1a1a1a", foreground="white", font=("Segoe UI", 15, "bold"))
        style.configure("Sub.TLabel", background="#1a1a1a", foreground="#999", font=("Segoe UI", 10))
        style.configure("TLabel", background="#1a1a1a", foreground="#ccc", font=("Segoe UI", 11))
        style.configure("green.Horizontal.TProgressbar", troughcolor="#2a2a2a", background="#1db954", thickness=12)
        style.configure("blue.Horizontal.TProgressbar", troughcolor="#2a2a2a", background="#4aa3ff", thickness=12)

        self.setup_background()

        self.status_badge = tk.Label(self, text="Idle", bg="#222", fg="#ccc",
                                     font=("Segoe UI", 9, "bold"), padx=10, pady=4)
        self.status_badge.place(x=700, y=10)

        card = ttk.Frame(self, style="Card.TFrame", padding=24)
        card.place(x=20, y=20, width=740, height=520)

        top_row = tk.Frame(card, bg="#1a1a1a")
        top_row.pack(fill="x")

        left_top = tk.Frame(top_row, bg="#1a1a1a")
        left_top.pack(side="left", fill="x", expand=True)

        self.header = ttk.Label(left_top, text="Preparing…", style="Header.TLabel")
        self.header.pack(side="left", anchor="w")

        self.ring = tk.Canvas(top_row, width=90, height=90, bg="#1a1a1a", highlightthickness=0)
        self.ring.pack(side="right")

        self.playlist_bar = ttk.Progressbar(card, style="green.Horizontal.TProgressbar", length=720, maximum=100)
        self.playlist_bar.pack(pady=(10,20))

        self.song_label = ttk.Label(card, text="Idle", font=("Segoe UI", 12, "bold"))
        self.song_label.pack(anchor="w")

        self.song_bar = ttk.Progressbar(card, style="blue.Horizontal.TProgressbar", length=720, maximum=100)
        self.song_bar.pack(pady=(10,5))

        self.progress_line = ttk.Label(card, text="", style="Sub.TLabel")
        self.progress_line.pack(anchor="w", pady=(0,10))

        return_frame = tk.Frame(card, bg="#0b0b0b", bd=1, relief="solid")
        return_frame.pack(fill="both", expand=True)

        self.terminal = tk.Text(return_frame, bg="#0b0b0b", fg="#7CFF7C", insertbackground="white",
                                font=("Consolas", 10), height=9, relief="flat")
        self.terminal.pack(fill="both", expand=True)
        self.terminal.configure(state="disabled")

        self.setup_tags()

        threading.Thread(target=self.run, daemon=True).start()
        self.animate_bars()

        self.status_badge.lift()
        self.bind("<Configure>", lambda e: self.status_badge.lift())

    def on_closing(self):
        
        self.is_running = False
        self.destroy()
        

    def setup_background(self):
        try:
            glass_path = CACHE_DIR / "glass.png"
            if glass_path.exists():
                try:
                    glass_img = Image.open(glass_path)
                except Exception:
                    
                    glass_path.unlink(missing_ok=True)
                    glass_img = self._create_glass_img(glass_path)
            else:
                glass_img = self._create_glass_img(glass_path)
            
            self.glass_bg = ImageTk.PhotoImage(glass_img)
            tk.Label(self, image=self.glass_bg, bg="#0e0e0e").place(x=10, y=10)

            vignette_path = CACHE_DIR / f"vignette_r{BLUR_RADIUS}.png"
            if vignette_path.exists():
                try:
                    vignette = Image.open(vignette_path)
                except Exception:
                    vignette_path.unlink(missing_ok=True)
                    vignette = self._create_vignette(vignette_path)
            else:
                vignette = self._create_vignette(vignette_path)

            self.vignette_img = ImageTk.PhotoImage(vignette)
            self.vignette_label = tk.Label(self, image=self.vignette_img, bg="#0e0e0e")
            self.vignette_label.place(x=0, y=0)
            self.vignette_label.lower()
        except Exception:
           
            pass

    def _create_glass_img(self, path):
        img = Image.new("RGBA", (760, 540), (255, 255, 255, 30))
        img = img.filter(ImageFilter.GaussianBlur(8))
        try:
            img.save(path)
        except OSError: pass
        return img

    def _create_vignette(self, path):
        width, height = 780, 580
        vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(vignette)
        
        for y in range(height):
            r = int(20 * (1 - y/height))
            g = int(20 * (1 - y/height))
            b = int(24 * (1 - y/height))
            draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

        for i in range(250):
            alpha = int(150 * (i / 250))
            draw.ellipse((-i, -i, width + i, height + i), outline=(0, 0, 0, alpha))
        
        vignette = vignette.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
        try:
            vignette.save(path)
        except OSError: pass
        return vignette

    def setup_tags(self):
        self.terminal.tag_config("song", foreground="#00bfff", font=("Consolas", 13, "bold"))
        self.terminal.tag_config("start", foreground="#00bfff", font=("Consolas", 13, "bold"))
        self.terminal.tag_config("info", foreground="#bbbbbb")
        self.terminal.tag_config("download", foreground="#1db954")
        self.terminal.tag_config("merge", foreground="#ffcc00")
        self.terminal.tag_config("final", foreground="#ff66cc")
        self.terminal.tag_config("done", foreground="#4aa3ff", font=("Consolas", 13, "bold"))
        self.terminal.tag_config("error", foreground="#ff4444", font=("Consolas", 10, "bold"))
        self.terminal.tag_config("retry", foreground="#ffaa00", font=("Consolas", 10, "bold"))
        self.terminal.tag_config("warn", foreground="#ff8888", font=("Consolas", 10, "bold"))

    def set_status(self, tag):
        if not self.is_running: return
        text = STATUS_TEXT.get(tag)
        if text:
            
            try:
                self.after(0, lambda: self.status_badge.config(text=text))
            except Exception: pass

    def pulse_icon(self):
        if not self.is_running: return
        
        if not self.current_icon or not self.pulse_tag or self.pulse_running:
            return
        self.pulse_running = True
        current_tag = self.pulse_tag

        def _pulse():
            if not self.is_running: return
            if self.pulse_tag != current_tag:
                self.pulse_running = False
                return
            self.icon_pulse = not self.icon_pulse
            size = 11 if self.icon_pulse else 10
            try:
                self.terminal.tag_config(current_tag, font=("Consolas", size, "bold"))
                self.after(400, _pulse)
            except Exception: pass

        self.after(0, _pulse)

    def log(self, text, tag="info"):
        if not self.is_running: return
        
        ts = time.strftime("%H:%M:%S")
        
        def _log_main_thread():
            if not self.is_running: return
            try:
               
                icon = STAGE_ICONS.get(tag, "")
                if icon:
                    self.current_icon = icon
                
              
                pulse_tag = f"pulse_{int(time.time()*1000)}"
                self.pulse_tag = pulse_tag
                self.terminal.tag_config(pulse_tag, font=("Consolas", 10, "bold"))
                
                self.terminal.configure(state="normal")
                if tag == "song":
                    self.terminal.insert("end", f"[{ts}] {icon} {text}\n", ("song",))
                else:
                    self.terminal.insert("end", f"[{ts}] {icon} {text}\n", (tag, pulse_tag))
                self.terminal.see("end")
                self.terminal.configure(state="disabled")
            except Exception: pass

        self.set_status(tag)
        self.after(0, _log_main_thread)
        self.after(100, self.pulse_icon)

    def animate_bars(self):
        if not self.is_running: return
        try:
            diff_p = self.target_playlist_pct - self.current_playlist_pct
            if abs(diff_p) > 0.1:
                self.current_playlist_pct += diff_p * 0.1
                self.playlist_bar.config(value=self.current_playlist_pct)
            else:
                self.current_playlist_pct = self.target_playlist_pct
                self.playlist_bar.config(value=self.target_playlist_pct)

            diff_s = self.target_song_pct - self.current_song_pct
            if abs(diff_s) > 0.1:
                self.current_song_pct += diff_s * 0.2
                self.song_bar.config(value=self.current_song_pct)
            else:
                self.current_song_pct = self.target_song_pct
                self.song_bar.config(value=self.target_song_pct)

            self.after(20, self.animate_bars)
        except Exception: pass

    def update_ring(self, progress, eta):
        if not self.is_running: return
        try:
            self.ring.delete("all")
            angle = progress * 3.6
            self.ring.create_oval(8, 8, 82, 82, outline="#333", width=5)
            self.ring.create_arc(8, 8, 82, 82, start=90, extent=-angle, style="arc", outline="#1db954", width=5)
            self.ring.create_text(45, 45, text=format_duration(eta), fill="white", font=("Segoe UI", 8, "bold"))
        except Exception: pass

    def check_stop(self):
        return not self.is_running

    def run(self):
        try:
            playlist_name, songs = get_playlist_info(PLAYLIST_URL)
        except Exception as e:
            self.log(str(e), "error")
            return

        if not self.is_running: return

        if not songs:
            self.log("Playlist is empty.", "error")
            return

        playlist_dir = BASE_DIR / playlist_name
        try:
            playlist_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log(f"Cannot create dir: {e}", "error")
            return

        total = len(songs)
        self.after(0, lambda: self.header.config(text=f"Downloading • {playlist_name}"))

        for i, song in enumerate(songs, 1):
            if not self.is_running: break
            
            start_time = time.time()

            self.after(0, lambda s=song: self.song_label.config(text=s))
            self.target_song_pct = 0
            self.current_song_pct = 0 
            self.after(0, lambda: self.song_bar.config(value=0))
            
            self.log(f"{song}", "song")

            def stage_cb(text, tag): self.log(text, tag)

            def progress_cb(s, pct, done, total_b, speed, eta):
                if not self.is_running: return
                mb_done = done / 1024 / 1024
                mb_total = total_b / 1024 / 1024 if total_b else 0
                spd = f"{speed/1024/1024:.2f} MB/s" if speed else "—"
                
                self.target_song_pct = pct
                
                try:
                    self.after(0, lambda: self.progress_line.config(
                        text=f"{mb_done:.2f}/{mb_total:.2f} MB   {spd}   Song ETA {format_duration(eta)}"
                    ))
                except Exception: pass

           
            ok = download_song(song, playlist_dir, progress_cb, stage_cb, self.check_stop)
            if not ok:
                self.failed_songs.append(song)

            duration = time.time() - start_time
            self.after(0, lambda d=duration: self.song_times.append(d))

            avg = sum(self.song_times) / len(self.song_times) if self.song_times else 0
            remaining = total - i
            playlist_eta = avg * remaining
            progress = (i / total) * 100
            
            self.target_playlist_pct = progress
            self.after(0, lambda p=progress, e=playlist_eta: self.update_ring(p, e))
            
            if ok:
                self.log(f"Finished {song}", "done")

        if not self.is_running: return

        self.log("Initial pass complete. Rescanning for missing files…", "retry")

      
        try:
            existing_files = {f.stem for f in playlist_dir.glob("*.mp3")}
        except OSError:
            existing_files = set()

        missing = [s for s in songs if s not in existing_files]

        if missing:
            self.log(f"{len(missing)} missing — retrying…", "retry")
            still_missing = []

            for song in missing:
                if not self.is_running: break
                self.log(f"Retrying {song}", "retry")
                ok = download_song(song, playlist_dir, progress_cb, stage_cb, self.check_stop)
                if not ok:
                    still_missing.append(song)

            if still_missing:
                self.log("Manual download required for:", "warn")
                for s in still_missing:
                    self.log(f"  {s}", "warn")
                self.log("These failed due to YouTube restrictions or network issues.", "warn")
            else:
                self.log("All retries succeeded.", "done")
        else:
            self.log("No missing files detected.", "done")

        self.log("All downloads finished.", "done")

if __name__ == "__main__":
    App().mainloop()