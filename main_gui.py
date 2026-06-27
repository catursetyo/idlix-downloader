import tkinter as tk
from tkinter import ttk, simpledialog
import queue
import shutil
import threading
import subprocess
import os
import time
import webbrowser
from io import BytesIO

from PIL import Image, ImageTk
import requests

from src.idlixHelper import IdlixHelper, logger

# ============================================================
# RETRY logic (same as CLI)
# ============================================================
RETRY_LIMIT = 3


def retry(func, *args, **kwargs):
    for _ in range(RETRY_LIMIT):
        result = func(*args, **kwargs)
        if result and result.get("status"):
            return result
        time.sleep(1)
    return {"status": False, "message": "Maximum retry reached"}


# ============================================================
# GUI LOGGER
# ============================================================
class GuiLogger:
    def __init__(self, log_queue):
        self.log_queue = log_queue

    def write(self, msg):
        if msg:
            self.log_queue.put(msg)

    def flush(self):
        pass


# ============================================================
# MAIN GUI
# ============================================================
class IdlixGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("IDLIX Downloader & Player GUI")
        self.root.geometry("1400x650")

        self.featured_movies = []
        self.poster_images = []
        self.log_queue = queue.Queue()
        self.ui_queue = queue.Queue()
        self.ffplay_process = None

        # Main container
        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill="both", expand=True)

        # LEFT = poster grid
        left_panel = ttk.Frame(main_frame)
        left_panel.grid(row=0, column=0, sticky="nsew")
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(0, weight=1)

        ttk.Label(left_panel, text="Featured Movies", font=("Arial", 16, "bold")).pack(anchor="w")

        self.poster_canvas = tk.Canvas(left_panel, bg="#181818")
        scrollbar = ttk.Scrollbar(left_panel, orient="vertical", command=self.poster_canvas.yview)
        self.poster_canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.poster_canvas.pack(side="left", fill="both", expand=True)

        self.poster_frame = ttk.Frame(self.poster_canvas)
        self.poster_canvas.create_window((0, 0), window=self.poster_frame, anchor="nw")

        self.poster_canvas.bind(
            "<Configure>",
            lambda e: self.poster_canvas.configure(scrollregion=self.poster_canvas.bbox("all"))
        )
        self.poster_frame.bind(
            "<Configure>",
            lambda e: self.poster_canvas.configure(scrollregion=self.poster_canvas.bbox("all"))
        )

        # RIGHT = controls + log
        right_panel = ttk.Frame(main_frame, padding=(10, 0))
        right_panel.grid(row=0, column=1, sticky="ns")
        right_panel.grid_propagate(False)

        ttk.Label(right_panel, text="Controls", font=("Arial", 16, "bold")).pack(anchor="w", pady=(0, 10))

        ttk.Button(right_panel, text="Refresh Featured", command=self.refresh_featured).pack(fill="x", pady=4)
        ttk.Button(right_panel, text="Download by URL", command=self.download_by_url).pack(fill="x", pady=4)
        ttk.Button(right_panel, text="Play by URL", command=self.play_by_url).pack(fill="x", pady=4)
        ttk.Button(right_panel, text="Stop Player", command=self.stop_player).pack(fill="x", pady=4)
        ttk.Button(right_panel, text="Open Downloads Folder", command=self.open_download_folder).pack(fill="x", pady=4)
        ttk.Button(right_panel, text="Clear Log", command=self.clear_log).pack(fill="x", pady=4)

        ttk.Label(right_panel, text="Log Output", font=("Arial", 14, "bold")).pack(anchor="w", pady=(20, 5))

        self.log_box = tk.Text(right_panel, height=28, state='disabled', bg="#111", fg="#0f0")
        self.log_box.pack(fill="both", expand=True)

        # Logger injection
        logger.remove()
        logger.add(GuiLogger(self.log_queue), format="{time:HH:mm:ss} | {level} | {message}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.drain_queues()

        # Load posters initially
        self.refresh_featured()

    def call_on_ui(self, callback, *args, **kwargs):
        self.ui_queue.put((callback, args, kwargs))

    def drain_queues(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_box.configure(state='normal')
                self.log_box.insert(tk.END, msg)
                self.log_box.see(tk.END)
                self.log_box.configure(state='disabled')
        except queue.Empty:
            pass

        try:
            while True:
                callback, args, kwargs = self.ui_queue.get_nowait()
                callback(*args, **kwargs)
        except queue.Empty:
            pass

        try:
            self.root.after(100, self.drain_queues)
        except tk.TclError:
            pass

    # ============================================================
    # POSTER GRID
    # ============================================================
    def show_poster_grid(self, poster_items):
        for w in self.poster_frame.winfo_children():
            w.destroy()

        self.poster_images.clear()
        self.featured_movies = [item["movie"] for item in poster_items]

        posters_per_row = 4
        row = col = 0

        for item in poster_items:
            movie = item["movie"]
            img = item["image"]

            frame = ttk.Frame(self.poster_frame)
            frame.grid(row=row, column=col, padx=10, pady=10)

            if img:
                tk_img = ImageTk.PhotoImage(img)
                self.poster_images.append(tk_img)

                tk.Button(
                    frame,
                    image=tk_img,
                    relief="flat",
                    command=lambda m=movie: self.on_poster_click(m)
                ).pack()
            else:
                tk.Button(
                    frame,
                    text="No Poster",
                    width=18,
                    height=12,
                    command=lambda m=movie: self.on_poster_click(m)
                ).pack()

            ttk.Label(
                frame,
                text=movie["title"],
                wraplength=150,
                justify="center"
            ).pack()

            col += 1
            if col >= posters_per_row:
                col = 0
                row += 1

        self.poster_canvas.configure(scrollregion=self.poster_canvas.bbox("all"))

    def load_poster_items(self, featured_movies):
        poster_items = []
        size = (150, 210)

        for movie in featured_movies:
            image = None
            poster_url = movie.get("poster")
            if poster_url:
                try:
                    response = requests.get(poster_url, timeout=8)
                    response.raise_for_status()
                    image = Image.open(BytesIO(response.content)).convert("RGB").resize(size)
                except Exception as exc:
                    logger.warning(f"Poster unavailable for {movie['title']}: {exc}")

            poster_items.append({
                "movie": movie,
                "image": image,
            })

        return poster_items

    # ============================================================
    # POSTER POPUP MENU
    # ============================================================
    def on_poster_click(self, movie):
        popup = tk.Toplevel(self.root)
        popup.title(movie["title"])
        popup.geometry("350x220")

        ttk.Label(popup, text=movie["title"], font=("Arial", 12, "bold")).pack(pady=10)

        ttk.Button(
            popup,
            text="Play",
            width=20,
            command=lambda: [popup.destroy(), self.process_movie(movie["url"], "play")]
        ).pack(pady=5)

        ttk.Button(
            popup,
            text="Download",
            width=20,
            command=lambda: [popup.destroy(), self.process_movie(movie["url"], "download")]
        ).pack(pady=5)

        ttk.Button(popup, text="Cancel", width=20, command=popup.destroy).pack(pady=10)

    # Variant selector
    def ask_variant(self, choices):
        popup = tk.Toplevel(self.root)
        popup.title("Select Resolution")
        popup.geometry("300x350")

        ttk.Label(popup, text="Select Variant", font=("Arial", 12, "bold")).pack(pady=10)

        listbox = tk.Listbox(popup, width=30, height=12)
        for c in choices:
            listbox.insert(tk.END, c)
        listbox.pack()

        result = {"res": None}

        def choose():
            sel = listbox.curselection()
            if sel:
                result["res"] = listbox.get(sel[0])
            popup.destroy()

        ttk.Button(popup, text="OK", command=choose).pack(pady=10)
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)

        popup.grab_set()
        self.root.wait_window(popup)

        return result["res"]

    # ============================================================
    # REFRESH FEATURED LIST
    # ============================================================
    def refresh_featured(self):
        def task():
            logger.info("Loading featured movies...")
            idlix = IdlixHelper()
            home = retry(idlix.get_home)

            if not home.get("status"):
                logger.error(f"Failed: {home.get('message')}")
                return

            poster_items = self.load_poster_items(home["featured_movie"])
            self.call_on_ui(self.show_poster_grid, poster_items)

            logger.success("Featured loaded.")

        threading.Thread(target=task, daemon=True).start()

    # ============================================================
    # URL BUTTON ACTIONS
    # ============================================================
    def download_by_url(self):
        url = simpledialog.askstring("Download Movie", "Enter movie URL:")
        if url:
            self.process_movie(url.strip(), "download")

    def play_by_url(self):
        url = simpledialog.askstring("Play Movie", "Enter movie URL:")
        if url:
            self.process_movie(url.strip(), "play")

    # ============================================================
    # CORE PROCESS (100% same as CLI)
    # ============================================================
    def process_movie(self, url: str, mode: str):

        def task():
            idlix = IdlixHelper()

            # 1. get video data
            video_data = retry(idlix.get_video_data, url)
            if not video_data.get("status"):
                logger.error(f"Error getting video data: {video_data.get('message')}")
                return

            logger.info(
                f"Video ID: {video_data['video_id']} | Name: {video_data['video_name']}"
            )

            # 2. embed URL
            embed = retry(idlix.get_embed_url)
            if not embed.get("status"):
                logger.error(f"Error getting embed URL: {embed.get('message')}")
                return

            logger.success(f"Embed: {embed['embed_url']}")

            # 3. m3u8
            m3u8 = retry(idlix.get_m3u8_url)
            if not m3u8.get("status"):
                logger.error(f"Error getting M3U8 URL: {m3u8.get('message')}")
                return

            logger.success(f"M3U8: {m3u8['m3u8_url']}")

            # 4. variant playlist
            if m3u8.get("is_variant_playlist"):
                choices = [
                    f"{v['id']} - {v['resolution']}" for v in m3u8["variant_playlist"]
                ]

                selected = None
                selected_event = threading.Event()

                def ask():
                    nonlocal selected
                    selected = self.ask_variant(choices)
                    selected_event.set()

                self.call_on_ui(ask)
                selected_event.wait()

                if not selected:
                    logger.warning("Variant selection cancelled.")
                    return

                selected_id = selected.split(" - ")[0]

                for v in m3u8["variant_playlist"]:
                    if str(v["id"]) == selected_id:
                        idlix.set_m3u8_url(v["uri"])
                        logger.success(f"Variant selected: {v['resolution']}")
                        break
            else:
                logger.warning("No variant playlist.")

            # PLAY
            if mode == "play":
                subtitle = idlix.get_subtitle()

                subtitle_file = subtitle["subtitle"] if subtitle.get("status") else None

                self.call_on_ui(self.start_ffplay, idlix.m3u8_url, subtitle_file, idlix.video_name)

            # DOWNLOAD
            else:
                result = idlix.download_m3u8()
                if result.get("status"):
                    logger.success(f"Downloaded: {result['path']}")
                else:
                    logger.error(f"Download failed: {result.get('message')}")

        threading.Thread(target=task, daemon=True).start()

    # ============================================================
    # ffplay controls
    # ============================================================
    def start_ffplay(self, m3u8_url, subtitle=None, title="IDLIX Player"):

        self.stop_player()

        ffplay_path = shutil.which("ffplay")
        if not ffplay_path:
            logger.error("ffplay not found. Please install ffmpeg first.")
            return

        args = [ffplay_path, "-i", m3u8_url, "-window_title", title, "-loglevel", "panic"]

        if subtitle:
            args += ["-vf", f"subtitles={subtitle}"]

        logger.info("Opening ffplay...")
        self.ffplay_process = subprocess.Popen(args)

    def stop_player(self):
        if self.ffplay_process and self.ffplay_process.poll() is None:
            try:
                self.ffplay_process.terminate()
                logger.info("ffplay terminated.")
            except:
                pass

    def open_download_folder(self):
        webbrowser.open(os.getcwd())

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete(1.0, tk.END)
        self.log_box.configure(state="disabled")

    def on_close(self):
        self.stop_player()
        self.root.destroy()


# ============================================================
# RUN APP
# ============================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = IdlixGUI(root)
    root.mainloop()
