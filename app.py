from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk

from yt_dlp import YoutubeDL


DEFAULT_FORMATS = {
    "Best single file": "best[ext=mp4]/best",
    "1080p or lower": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
    "720p or lower": "best[height<=720][ext=mp4]/best[height<=720]/best",
    "480p or lower": "best[height<=480][ext=mp4]/best[height<=480]/best",
    "360p or lower": "best[height<=360][ext=mp4]/best[height<=360]/best",
    "Audio only": "bestaudio/best",
}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
DEFAULT_WORKERS = 4
SHORTCUT_EVENTS = {
    "paste": "<<Paste>>",
    "copy": "<<Copy>>",
    "cut": "<<Cut>>",
    "select_all": "<<SelectAll>>",
}
SHORTCUT_KEYS = {
    "a": "select_all",
    "ф": "select_all",
    "c": "copy",
    "с": "copy",
    "v": "paste",
    "м": "paste",
    "x": "cut",
    "ч": "cut",
}
SHORTCUT_KEYSYMS = {
    "cyrillic_ef": "select_all",
    "cyrillic_capital_ef": "select_all",
    "cyrillic_es": "copy",
    "cyrillic_capital_es": "copy",
    "cyrillic_em": "paste",
    "cyrillic_capital_em": "paste",
    "cyrillic_che": "cut",
    "cyrillic_capital_che": "cut",
}
MAC_SHORTCUT_KEYCODES = {
    0: "select_all",
    7: "cut",
    8: "copy",
    9: "paste",
}


class TkLogger:
    def __init__(self, app, prefix=""):
        self.app = app
        self.prefix = prefix

    def debug(self, message):
        pass

    def info(self, message):
        self.app.add_log(f"{self.prefix}{message}")

    def warning(self, message):
        self.app.add_log(f"{self.prefix}Warning: {message}")

    def error(self, message):
        self.app.add_log(f"{self.prefix}Error: {message}")


class DownloaderApp(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=16)
        self.master = master
        self.messages = queue.Queue()
        self.quality_formats = dict(DEFAULT_FORMATS)
        self.url_rows = []
        self.is_busy = False
        self.last_edit_widget = None

        self.output_var = tk.StringVar(value=str(Path.cwd() / "downloads"))
        self.folder_name_var = tk.StringVar()
        self.quality_var = tk.StringVar(value="Best single file")
        self.worker_count_var = tk.StringVar(value=str(DEFAULT_WORKERS))
        self.playlist_var = tk.BooleanVar(value=False)
        self.ignore_ssl_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.DoubleVar(value=0)

        self._configure_edit_menu()
        self._build_ui()
        self._poll_messages()

    def _configure_edit_menu(self):
        menu = tk.Menu(self.master)
        edit_menu = tk.Menu(menu, tearoff=False)
        edit_menu.add_command(
            label="Cut",
            accelerator="Command+X",
            command=lambda: self._run_shortcut("cut"),
        )
        edit_menu.add_command(
            label="Copy",
            accelerator="Command+C",
            command=lambda: self._run_shortcut("copy"),
        )
        edit_menu.add_command(
            label="Paste",
            accelerator="Command+V",
            command=lambda: self._run_shortcut("paste"),
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Select All",
            accelerator="Command+A",
            command=lambda: self._run_shortcut("select_all"),
        )
        menu.add_cascade(label="Edit", menu=edit_menu)
        self.master.configure(menu=menu)

    def _run_shortcut(self, action):
        widget = self.master.focus_get()
        if not self._is_shortcut_widget(widget):
            widget = self.last_edit_widget
        if self._can_run_shortcut(widget, action):
            widget.event_generate(SHORTCUT_EVENTS[action])

    def _shortcut_keypress(self, event):
        action = self._shortcut_action(event)
        if not action or not self._can_run_shortcut(event.widget, action):
            return None

        event.widget.event_generate(SHORTCUT_EVENTS[action])
        return "break"

    def _shortcut_action(self, event):
        keycode = getattr(event, "keycode", None)
        if keycode in MAC_SHORTCUT_KEYCODES:
            return MAC_SHORTCUT_KEYCODES[keycode]

        key = (getattr(event, "char", "") or "").lower()
        keysym = (getattr(event, "keysym", "") or "").lower()
        if key in SHORTCUT_KEYS:
            return SHORTCUT_KEYS[key]
        if keysym in SHORTCUT_KEYS:
            return SHORTCUT_KEYS[keysym]
        if keysym in SHORTCUT_KEYSYMS:
            return SHORTCUT_KEYSYMS[keysym]

        return None

    def _can_run_shortcut(self, widget, action):
        if not self._is_shortcut_widget(widget):
            return False
        if widget.winfo_class() == "Text":
            return action in {"copy", "select_all"}
        return True

    def _is_shortcut_widget(self, widget):
        return bool(
            widget
            and widget.winfo_exists()
            and widget.winfo_class() in {"Entry", "Spinbox", "Text", "TEntry", "TSpinbox"}
        )

    def _build_ui(self):
        self.master.title("Video Downloader")
        self.master.minsize(760, 560)
        self.grid(row=0, column=0, sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(8, weight=1)

        ttk.Label(self, text="Video URLs").grid(row=0, column=0, sticky="nw", pady=(0, 8))
        url_area = ttk.Frame(self)
        url_area.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 8))
        url_area.columnconfigure(0, weight=1)

        self.url_rows_frame = ttk.Frame(url_area)
        self.url_rows_frame.grid(row=0, column=0, sticky="ew")
        self.url_rows_frame.columnconfigure(0, weight=1)

        self.add_url_button = ttk.Button(url_area, text="+", width=3, command=self.add_url_row)
        self.add_url_button.grid(row=0, column=1, sticky="ne", padx=(8, 0))
        self.add_url_row()

        ttk.Label(self, text="Save to").grid(row=1, column=0, sticky="w", pady=(0, 8))
        self.output_entry = ttk.Entry(self, textvariable=self.output_var)
        self.output_entry.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        self._register_edit_widget(self.output_entry)
        self.browse_button = ttk.Button(self, text="Browse", command=self.choose_output)
        self.browse_button.grid(row=1, column=2, padx=(8, 0), pady=(0, 8))

        ttk.Label(self, text="Folder name").grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.folder_name_entry = ttk.Entry(self, textvariable=self.folder_name_var)
        self.folder_name_entry.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        self._register_edit_widget(self.folder_name_entry)

        ttk.Label(self, text="Quality").grid(row=3, column=0, sticky="w", pady=(0, 8))
        self.quality_box = ttk.Combobox(
            self,
            textvariable=self.quality_var,
            values=list(self.quality_formats),
            state="readonly",
        )
        self.quality_box.grid(row=3, column=1, sticky="ew", pady=(0, 8))
        self.check_button = ttk.Button(self, text="Check", command=self.fetch_formats)
        self.check_button.grid(row=3, column=2, padx=(8, 0), pady=(0, 8))

        ttk.Label(self, text="Threads").grid(row=4, column=0, sticky="w", pady=(0, 8))
        self.worker_spin = ttk.Spinbox(
            self,
            from_=1,
            to=32,
            textvariable=self.worker_count_var,
            width=8,
        )
        self.worker_spin.grid(row=4, column=1, sticky="w", pady=(0, 8))
        self._register_edit_widget(self.worker_spin)

        ttk.Checkbutton(
            self,
            text="Download playlist",
            variable=self.playlist_var,
        ).grid(row=5, column=1, sticky="w", pady=(0, 12))

        ttk.Checkbutton(
            self,
            text="Ignore SSL certificate errors",
            variable=self.ignore_ssl_var,
        ).grid(row=5, column=2, sticky="e", pady=(0, 12))

        actions = ttk.Frame(self)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        actions.columnconfigure(1, weight=1)

        self.download_button = ttk.Button(actions, text="Download", command=self.download)
        self.download_button.grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(actions, variable=self.progress_var, maximum=100)
        self.progress.grid(row=0, column=1, sticky="ew", padx=12)

        ttk.Label(actions, textvariable=self.status_var).grid(row=0, column=2, sticky="e")

        ttk.Label(self, text="Log").grid(row=7, column=0, sticky="w")
        self.log = tk.Text(self, height=12, wrap="word", state="disabled")
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        self._register_edit_widget(self.log)

    def add_url_row(self, value=""):
        row = ttk.Frame(self.url_rows_frame)
        row.columnconfigure(0, weight=1)

        url_var = tk.StringVar(value=value)
        url_entry = ttk.Entry(row, textvariable=url_var)
        url_entry.grid(row=0, column=0, sticky="ew")
        self._register_edit_widget(url_entry)

        remove_button = ttk.Button(row, text="-", width=3)
        remove_button.configure(command=lambda: self.remove_url_row(row))
        remove_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

        self.url_rows.append({
            "frame": row,
            "var": url_var,
            "entry": url_entry,
            "remove_button": remove_button,
        })
        self._refresh_url_rows()
        url_entry.focus()

    def _register_edit_widget(self, widget):
        widget.bind("<FocusIn>", self._remember_edit_widget, add=True)
        widget.bind("<Mod1-KeyPress>", self._shortcut_keypress, add=True)
        widget.bind("<Control-KeyPress>", self._shortcut_keypress, add=True)

    def _remember_edit_widget(self, event):
        self.last_edit_widget = event.widget

    def remove_url_row(self, row):
        if len(self.url_rows) == 1:
            self.url_rows[0]["var"].set("")
            return

        for index, item in enumerate(self.url_rows):
            if item["frame"] == row:
                item["frame"].destroy()
                del self.url_rows[index]
                break

        self._refresh_url_rows()

    def _refresh_url_rows(self):
        for index, item in enumerate(self.url_rows):
            item["frame"].grid(row=index, column=0, sticky="ew", pady=(0, 6))

    def choose_output(self):
        initial = self.output_var.get().strip() or str(Path.cwd())
        initial_path = Path(initial).expanduser()
        initial_dir = initial_path.parent if initial_path.suffix else initial_path

        selected = filedialog.askdirectory(
            title="Choose download folder",
            initialdir=str(initial_dir),
            mustexist=False,
        )
        if selected:
            self.output_var.set(selected)

    def fetch_formats(self):
        if self.is_busy:
            return

        urls = self._urls()
        if not urls:
            messagebox.showwarning("Missing URL", "Paste at least one video URL first.")
            return

        self._set_busy(True)
        self.status_var.set("Checking formats...")
        self.add_log(f"Checking available formats for first URL: {urls[0]}")
        download_playlist = self.playlist_var.get()
        ignore_ssl = self.ignore_ssl_var.get()
        threading.Thread(
            target=self._fetch_formats_worker,
            args=(urls[0], download_playlist, ignore_ssl),
            daemon=True,
        ).start()

    def download(self):
        if self.is_busy:
            return

        urls = self._urls()
        if not urls:
            messagebox.showwarning("Missing URL", "Paste at least one video URL first.")
            return

        base_output = self.output_var.get().strip()
        if not base_output:
            messagebox.showwarning("Missing destination", "Choose where to save the videos.")
            return

        output = self._download_directory(base_output)

        selected_quality = self.quality_var.get()
        selected_format = self.quality_formats.get(selected_quality)
        if not selected_format:
            messagebox.showwarning("Missing quality", "Choose a download quality.")
            return

        worker_count = self._worker_count(len(urls))
        self._set_busy(True)
        self.progress_var.set(0)
        self.status_var.set("Starting...")
        self.add_log(f"Download started: {len(urls)} URL(s), {worker_count} worker(s).")
        self.add_log(f"Download folder: {output}")

        download_playlist = self.playlist_var.get()
        ignore_ssl = self.ignore_ssl_var.get()
        threading.Thread(
            target=self._download_worker,
            args=(urls, output, selected_format, download_playlist, ignore_ssl, worker_count),
            daemon=True,
        ).start()

    def _fetch_formats_worker(self, url, download_playlist, ignore_ssl):
        try:
            options = {
                "quiet": True,
                "logger": TkLogger(self),
                "noplaylist": not download_playlist,
                "nocheckcertificate": ignore_ssl,
            }
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)

            item = self._first_video(info)
            formats = self._format_choices(item.get("formats", []))
            if not formats:
                self.messages.put(("warning", "No combined video formats found. Default presets are still available."))
                return

            self.messages.put(("formats", formats))
        except Exception as exc:
            self.messages.put(("error", self._format_error("Could not check formats", exc)))
        finally:
            self.messages.put(("busy", False))

    def _download_worker(self, urls, output, selected_format, download_playlist, ignore_ssl, worker_count):
        progress_by_index = {index: 0 for index in range(len(urls))}
        progress_lock = threading.Lock()
        success_count = 0
        error_count = 0

        try:
            output_template = self._output_template(output, len(urls))
            Path(output_template).expanduser().parent.mkdir(parents=True, exist_ok=True)

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(
                        self._download_one,
                        index,
                        url,
                        len(urls),
                        output_template,
                        selected_format,
                        download_playlist,
                        ignore_ssl,
                        self._progress_hook(index, len(urls), progress_by_index, progress_lock),
                    ): (index, url)
                    for index, url in enumerate(urls)
                }

                for future in as_completed(futures):
                    index, url = futures[future]
                    try:
                        future.result()
                        success_count += 1
                        self.messages.put(("log", f"[{index + 1}/{len(urls)}] Finished: {url}"))
                    except Exception as exc:
                        error_count += 1
                        self.messages.put(("log", self._format_error(f"[{index + 1}/{len(urls)}] Download failed", exc)))
                    finally:
                        with progress_lock:
                            progress_by_index[index] = 100
                            aggregate = sum(progress_by_index.values()) / len(urls)
                        self.messages.put(("progress", aggregate))
                        self.messages.put(("status", f"{success_count + error_count}/{len(urls)} done"))

            self.messages.put(("progress", 100))
            if error_count:
                self.messages.put(("error", f"Finished with {error_count} failed download(s) and {success_count} successful download(s)."))
            else:
                self.messages.put(("status", "Done"))
                self.messages.put(("log", "All downloads finished."))
        except Exception as exc:
            self.messages.put(("error", self._format_error("Download failed", exc)))
        finally:
            self.messages.put(("busy", False))

    def _download_one(
        self,
        index,
        url,
        total_urls,
        output_template,
        selected_format,
        download_playlist,
        ignore_ssl,
        progress_hook,
    ):
        prefix = f"[{index + 1}/{total_urls}] "
        self.messages.put(("log", f"{prefix}Starting: {url}"))

        options = {
            "format": selected_format,
            "outtmpl": output_template,
            "noplaylist": not download_playlist,
            "nocheckcertificate": ignore_ssl,
            "logger": TkLogger(self, prefix),
            "progress_hooks": [progress_hook],
        }

        with YoutubeDL(options) as ydl:
            ydl.download([url])

    def _output_template(self, output, url_count):
        path = Path(output).expanduser()
        if url_count == 1 and path.suffix:
            return str(path.with_suffix("")) + ".%(ext)s"
        if path.suffix:
            path = path.with_suffix("")
        return str(path / "%(title)s.%(ext)s")

    def _download_directory(self, base_output):
        path = Path(base_output).expanduser()
        if path.suffix:
            path = path.with_suffix("")

        for part in self._folder_name_parts():
            path = path / part

        return str(path)

    def _folder_name_parts(self):
        folder_name = self.folder_name_var.get().strip()
        if not folder_name:
            return []

        return [
            part.strip()
            for part in Path(folder_name).parts
            if part.strip() and part not in {".", "..", Path(folder_name).anchor}
        ]

    def _first_video(self, info):
        entries = info.get("entries")
        if entries:
            for entry in entries:
                if entry:
                    return entry
        return info

    def _format_choices(self, formats):
        choices = {}
        seen = set()

        sorted_formats = sorted(
            formats,
            key=lambda item: (
                item.get("height") or 0,
                item.get("fps") or 0,
                item.get("tbr") or 0,
            ),
            reverse=True,
        )

        for item in sorted_formats:
            if item.get("vcodec") == "none" or item.get("acodec") == "none":
                continue

            format_id = item.get("format_id")
            if not format_id:
                continue

            height = item.get("height")
            fps = item.get("fps")
            ext = item.get("ext") or "video"
            tbr = item.get("tbr")
            resolution = f"{height}p" if height else item.get("resolution") or "video"
            label_parts = [resolution, ext]

            if fps:
                label_parts.append(f"{int(fps)}fps")
            if tbr:
                label_parts.append(f"{int(tbr)}k")

            label = f"{' '.join(label_parts)} (format {format_id})"
            if label in seen:
                continue

            seen.add(label)
            choices[label] = format_id

        return choices

    def _progress_hook(self, index, total_urls, progress_by_index, progress_lock):
        def hook(data):
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes") or 0
                if total:
                    percent = downloaded / total * 100
                    with progress_lock:
                        progress_by_index[index] = percent
                        aggregate = sum(progress_by_index.values()) / total_urls
                    self.messages.put(("progress", aggregate))
                    self.messages.put(("status", f"{aggregate:.1f}% total"))
                else:
                    self.messages.put(("status", "Downloading..."))
            elif status == "finished":
                with progress_lock:
                    progress_by_index[index] = 100
                    aggregate = sum(progress_by_index.values()) / total_urls
                filename = data.get("filename")
                if filename:
                    self.messages.put(("log", f"[{index + 1}/{total_urls}] Saved media file: {filename}"))
                self.messages.put(("progress", aggregate))
                self.messages.put(("status", "Processing..."))

        return hook

    def _format_error(self, prefix, exc):
        message = self._clean_log(str(exc))
        if "CERTIFICATE_VERIFY_FAILED" in message:
            message += "\n\nTry enabling \"Ignore SSL certificate errors\" and run the action again."
        return f"{prefix}: {message}"

    def _worker_count(self, url_count):
        try:
            requested = int(self.worker_count_var.get())
        except ValueError:
            requested = DEFAULT_WORKERS

        requested = max(1, requested)
        return min(requested, url_count)

    def _urls(self):
        urls = []
        for item in self.url_rows:
            url = item["var"].get().strip()
            if url:
                urls.append(url)
        return urls

    def _set_busy(self, value):
        self.is_busy = value
        state = "disabled" if value else "normal"
        readonly_state = "disabled" if value else "readonly"

        self.download_button.configure(state=state)
        self.check_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.add_url_button.configure(state=state)
        self.folder_name_entry.configure(state=state)
        self.worker_spin.configure(state=state)
        self.quality_box.configure(state=readonly_state)

        for item in self.url_rows:
            item["entry"].configure(state=state)
            item["remove_button"].configure(state=state)

    def _poll_messages(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "busy":
                    self._set_busy(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    self.progress_var.set(payload)
                elif kind == "log":
                    self.add_log(payload)
                elif kind == "warning":
                    self.status_var.set("Warning")
                    self.add_log(payload)
                    messagebox.showwarning("Warning", payload)
                elif kind == "error":
                    self.status_var.set("Error")
                    self.add_log(payload)
                    messagebox.showerror("Error", payload)
                elif kind == "formats":
                    self.quality_formats = dict(DEFAULT_FORMATS)
                    self.quality_formats.update(payload)
                    self.quality_box.configure(values=list(self.quality_formats))
                    first_format = next(iter(payload))
                    self.quality_var.set(first_format)
                    self.status_var.set("Formats loaded")
                    self.add_log(f"Loaded {len(payload)} available formats.")
        except queue.Empty:
            pass

        self.after(100, self._poll_messages)

    def add_log(self, message):
        if threading.current_thread() is not threading.main_thread():
            self.messages.put(("log", message))
            return

        message = self._clean_log(message)
        self.log.configure(state="normal")
        self.log.insert("end", f"{message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clean_log(self, message):
        return ANSI_RE.sub("", message)


def main():
    root = tk.Tk()
    DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
