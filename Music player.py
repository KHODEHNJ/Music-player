import json
import os
import random
import threading
import tempfile
import time
from datetime import date, datetime, timedelta
import tkinter as tk
from dataclasses import dataclass, asdict
from pathlib import Path
from tkinter import filedialog, messagebox

import pygame

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None


APP_NAME = "PLAY HONEY"
SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
BASE_DIR = Path(__file__).resolve().parent
SAVE_FILE = BASE_DIR / "play_honey_data.json"


class PlayerState:
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class Song:
    id: str
    path: str
    title: str
    artist: str
    duration: float = 0.0


class MetadataCache:
    """Caches metadata/duration so UI refreshes do not reread audio files."""

    def __init__(self):
        self._cache = {}

    def get(self, path):
        path = os.path.abspath(path)
        if path in self._cache:
            return self._cache[path]

        title = Path(path).stem
        artist = "Unknown Artist"
        duration = 0.0

        if MutagenFile is not None:
            try:
                audio = MutagenFile(path, easy=True)
                if audio:
                    tags = audio.tags or {}
                    title_tags = tags.get("title")
                    artist_tags = tags.get("artist")

                    if title_tags and title_tags[0]:
                        title = str(title_tags[0])
                    if artist_tags and artist_tags[0]:
                        artist = str(artist_tags[0])

                    if audio.info:
                        duration = float(getattr(audio.info, "length", 0) or 0)
            except (OSError, ValueError, TypeError):
                pass

        return self._store(path, title, artist, duration)

    def _store(self, path, title, artist, duration):
        item = {
            "title": title,
            "artist": artist,
            "duration": max(0.0, float(duration)),
        }
        self._cache[path] = item
        return item

    def invalidate(self, path=None):
        if path is None:
            self._cache.clear()
        else:
            self._cache.pop(os.path.abspath(path), None)


class PlaylistManager:
    """Playlist state uses stable Song IDs instead of fragile numeric references."""

    def __init__(self):
        self.songs = []
        self.current_id = None

    def add_paths(self, paths, metadata):
        existing = {song.path for song in self.songs}
        added = []

        for raw_path in paths:
            path = os.path.abspath(raw_path)
            if path in existing or not os.path.isfile(path):
                continue

            info = metadata.get(path)
            song = Song(
                id=os.urandom(8).hex(),
                path=path,
                title=info["title"],
                artist=info["artist"],
                duration=info["duration"],
            )
            self.songs.append(song)
            existing.add(path)
            added.append(song)

        return added

    def get_by_id(self, song_id):
        if song_id is None:
            return None
        return next((song for song in self.songs if song.id == song_id), None)

    def index_of(self, song_id):
        for index, song in enumerate(self.songs):
            if song.id == song_id:
                return index
        return None

    def remove_by_id(self, song_id):
        index = self.index_of(song_id)
        if index is None:
            return False

        removed = self.songs.pop(index)
        if removed.id == self.current_id:
            self.current_id = self.songs[index - 1].id if self.songs else None
        return True

    def move(self, old_index, new_index):
        if not (0 <= old_index < len(self.songs)):
            return
        if not (0 <= new_index < len(self.songs)):
            return
        if old_index == new_index:
            return

        song = self.songs.pop(old_index)
        self.songs.insert(new_index, song)

    def clear(self):
        self.songs.clear()
        self.current_id = None

    def valid(self):
        return [song for song in self.songs if os.path.isfile(song.path)]


class StateManager:
    """Safe JSON persistence with validation and atomic replacement."""

    VERSION = 2

    def __init__(self, path):
        self.path = Path(path)

    def save(self, data):
        payload = dict(data)
        payload["version"] = self.VERSION

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".play_honey_",
            suffix=".tmp",
            dir=str(self.path.parent),
            text=True,
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, self.path)
        except (OSError, TypeError, ValueError):
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def load(self):
        if not self.path.is_file():
            return {}

        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


class AudioPlayer:
    """Small pygame wrapper. UI code never talks to pygame directly."""

    def __init__(self):
        self.state = PlayerState.STOPPED
        self.volume = 0.75
        self.current_file = None
        self.duration = 0.0
        self.start_position = 0.0
        self.available = False
        self.last_error = ""

        try:
            pygame.mixer.init()
            pygame.mixer.music.set_volume(self.volume)
            self.available = True
        except pygame.error as exc:
            self.last_error = str(exc)

    def set_volume(self, value):
        self.volume = max(0.0, min(1.0, float(value)))
        if self.available:
            try:
                pygame.mixer.music.set_volume(self.volume)
            except pygame.error:
                pass

    def load(self, file_path, duration=0.0):
        if not self.available:
            return False

        try:
            pygame.mixer.music.load(file_path)
            self.current_file = file_path
            self.duration = max(0.0, float(duration))
            self.start_position = 0.0
            self.state = PlayerState.STOPPED
            return True
        except pygame.error as exc:
            self.last_error = str(exc)
            self.current_file = None
            self.duration = 0.0
            self.start_position = 0.0
            self.state = PlayerState.STOPPED
            return False

    def play(self, start=0.0):
        if not self.available or not self.current_file:
            return False

        start = max(0.0, min(float(start), self.duration or float(start)))
        try:
            pygame.mixer.music.play(loops=0, start=start)
            self.start_position = start
            self.state = PlayerState.PLAYING
            return True
        except (pygame.error, ValueError) as exc:
            self.last_error = str(exc)
            return False

    def pause(self):
        if self.state == PlayerState.PLAYING and self.available:
            self.start_position = self.get_position()
            try:
                pygame.mixer.music.pause()
                self.state = PlayerState.PAUSED
            except pygame.error:
                pass

    def resume(self):
        if self.state == PlayerState.PAUSED and self.available:
            try:
                pygame.mixer.music.unpause()
                self.state = PlayerState.PLAYING
            except pygame.error:
                pass

    def stop(self):
        if self.available:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass
        self.state = PlayerState.STOPPED
        self.start_position = 0.0

    def seek(self, position):
        if not self.available or not self.current_file:
            return False

        position = max(0.0, min(float(position), self.duration))
        try:
            pygame.mixer.music.play(loops=0, start=position)
            self.start_position = position
            self.state = PlayerState.PLAYING
            return True
        except (pygame.error, ValueError) as exc:
            self.last_error = str(exc)
            return False

    def get_position(self):
        if self.state not in (PlayerState.PLAYING, PlayerState.PAUSED):
            return self.start_position

        if not self.available:
            return self.start_position

        try:
            elapsed = pygame.mixer.music.get_pos()
        except pygame.error:
            return self.start_position

        if elapsed < 0:
            return self.start_position

        return min(self.duration or float("inf"), self.start_position + elapsed / 1000.0)

    def is_finished(self):
        if self.state != PlayerState.PLAYING or not self.available:
            return False

        try:
            busy = pygame.mixer.music.get_busy()
        except pygame.error:
            return False

        # At the exact end pygame can report get_pos() == -1.
        # Therefore the old position-based check could miss the end forever.
        if not busy:
            return self.duration > 0 and self.current_file is not None

        # Catch the tiny interval where the mixer is still busy but the
        # reported position has already reached the end.
        position = self.get_position()
        return self.duration > 0 and position >= self.duration - 0.15

    def cleanup(self):
        if not self.available:
            return
        try:
            pygame.mixer.music.stop()
        except pygame.error:
            pass
        try:
            pygame.mixer.quit()
        except pygame.error:
            pass
        self.available = False


class StatsManager:
    """Persistent weekly listening statistics."""

    VERSION = 1

    def __init__(self):
        self.weeks = {}

    @staticmethod
    def week_key(day=None):
        day = day or date.today()
        iso = day.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def _week(self, key=None):
        key = key or self.week_key()
        week = self.weeks.setdefault(key, {
            "listening_seconds": 0.0,
            "songs": {},
            "artists": {},
        })
        week.setdefault("listening_seconds", 0.0)
        week.setdefault("songs", {})
        week.setdefault("artists", {})
        return week

    def record_play(self, song):
        if song is None:
            return
        week = self._week()
        week["songs"][song.id] = {
            "title": song.title,
            "artist": song.artist,
            "path": song.path,
            "plays": int(week["songs"].get(song.id, {}).get("plays", 0)) + 1,
        }
        artist = song.artist or "Unknown Artist"
        week["artists"][artist] = int(week["artists"].get(artist, 0)) + 1

    def add_listening_time(self, seconds):
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        if seconds <= 0:
            return
        self._week()["listening_seconds"] += seconds

    def export(self):
        return {"version": self.VERSION, "weeks": self.weeks}

    def import_data(self, data):
        self.weeks = {}
        if not isinstance(data, dict):
            return
        raw = data.get("weeks", {})
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            songs = value.get("songs", {})
            artists = value.get("artists", {})
            if not isinstance(songs, dict) or not isinstance(artists, dict):
                continue
            self.weeks[str(key)] = {
                "listening_seconds": max(0.0, float(value.get("listening_seconds", 0) or 0)),
                "songs": songs,
                "artists": artists,
            }

    def weekly_rows(self, count=12):
        current = date.today()
        start = current - timedelta(days=current.weekday())
        rows = []
        for offset in range(count):
            monday = start - timedelta(days=7 * offset)
            key = self.week_key(monday)
            week = self._week(key)
            song_items = [v for v in week["songs"].values() if isinstance(v, dict)]
            artist_items = [(str(k), int(v)) for k, v in week["artists"].items()]
            song_items.sort(key=lambda x: int(x.get("plays", 0)), reverse=True)
            artist_items.sort(key=lambda x: x[1], reverse=True)
            sunday = monday + timedelta(days=6)
            rows.append({
                "key": key,
                "label": f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}",
                "song": song_items[0] if song_items else None,
                "artist": artist_items[0] if artist_items else None,
                "seconds": float(week.get("listening_seconds", 0.0)),
            })
        return rows


class MusicPlayer:
    THEMES = {
        "Honey": {
            "BG": "#090A0C", "PANEL": "#111317", "PANEL_2": "#171A20",
            "ACCENT": "#F5B942", "ACCENT_ACTIVE": "#FFD36A",
            "WHITE": "#F7F7F4", "GREY": "#8E929B", "DARK_GREY": "#292D35",
            "TEXT_ON_ACCENT": "#17130A",
        },
        "Midnight": {
            "BG": "#070A12", "PANEL": "#0E1320", "PANEL_2": "#151C2C",
            "ACCENT": "#8B9CFF", "ACCENT_ACTIVE": "#B1BCFF",
            "WHITE": "#F3F5FF", "GREY": "#8C95AA", "DARK_GREY": "#283149",
            "TEXT_ON_ACCENT": "#0A0D18",
        },
        "Ocean": {
            "BG": "#061013", "PANEL": "#0B1A1F", "PANEL_2": "#10262D",
            "ACCENT": "#35D0C4", "ACCENT_ACTIVE": "#6BE5DC",
            "WHITE": "#F1FBFA", "GREY": "#7F9A9E", "DARK_GREY": "#214047",
            "TEXT_ON_ACCENT": "#061313",
        },
        "Purple Night": {
            "BG": "#0C0811", "PANEL": "#171020", "PANEL_2": "#21172D",
            "ACCENT": "#B98AF7", "ACCENT_ACTIVE": "#D0AEFF",
            "WHITE": "#FAF7FF", "GREY": "#988DA5", "DARK_GREY": "#352746",
            "TEXT_ON_ACCENT": "#140B1D",
        },
        "Emerald": {
            "BG": "#070D0B", "PANEL": "#0E1814", "PANEL_2": "#14231D",
            "ACCENT": "#70D69A", "ACCENT_ACTIVE": "#98E9B6",
            "WHITE": "#F2FAF5", "GREY": "#899B91", "DARK_GREY": "#294036",
            "TEXT_ON_ACCENT": "#07120C",
        },
        "Cherry": {
            "BG": "#10080B", "PANEL": "#1A0F13", "PANEL_2": "#25151B",
            "ACCENT": "#E86A83", "ACCENT_ACTIVE": "#F28EA1",
            "WHITE": "#FFF5F7", "GREY": "#A18E94", "DARK_GREY": "#432731",
            "TEXT_ON_ACCENT": "#19080D",
        },
        "Graphite": {
            "BG": "#0B0D0F", "PANEL": "#14171B", "PANEL_2": "#1C2026",
            "ACCENT": "#AEB8C8", "ACCENT_ACTIVE": "#D0D8E5",
            "WHITE": "#F4F5F7", "GREY": "#8B919A", "DARK_GREY": "#303640",
            "TEXT_ON_ACCENT": "#101216",
        },
        "Ivory": {
            "BG": "#F3F0E9", "PANEL": "#FBFAF7", "PANEL_2": "#E9E5DC",
            "ACCENT": "#B77A24", "ACCENT_ACTIVE": "#D39A49",
            "WHITE": "#25221D", "GREY": "#746F66", "DARK_GREY": "#D7D1C6",
            "TEXT_ON_ACCENT": "#FFF9EF",
        },
    }

    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1200x750")
        self.root.minsize(950, 600)

        self.metadata = MetadataCache()
        self.playlist = PlaylistManager()
        self.player = AudioPlayer()
        self.state_manager = StateManager(SAVE_FILE)
        self.stats = StatsManager()
        self.stats_last_tick = time.monotonic()
        self.stats_window = None

        self.shuffle_mode = False
        self.repeat_song = False
        self.shuffle_queue = []
        self.shuffle_history = []

        self.search_query = ""
        self.filtered_indexes = []
        self.search_placeholder_active = True

        self.progress_dragging = False
        self.drag_start_visible_index = None
        self.drag_start_y = None
        self.dragging = False

        self.last_volume = 75.0
        self.is_muted = False
        self.resume_position = 0.0
        self.state_dirty = False
        self.closed = False

        self.current_title = tk.StringVar(value="NO SONG SELECTED")
        self.current_artist = tk.StringVar(value=APP_NAME)
        self.time_var = tk.StringVar(value="00:00 / 00:00")
        self.volume_var = tk.DoubleVar(value=75)
        self.search_var = tk.StringVar()

        self.theme_name = "Honey"
        self.apply_theme_values("Honey")

        self.build_ui()
        self.bind_shortcuts()

        self.load_state()

        if not self.player.available:
            self.root.after(250, self.show_audio_warning)

        self.update_loop()
        self.autosave_loop()

    # ---------- theme ----------

    def apply_theme_values(self, name):
        theme = self.THEMES[name]
        self.theme_name = name
        self.BG = theme["BG"]
        self.PANEL = theme["PANEL"]
        self.PANEL_2 = theme["PANEL_2"]
        self.YELLOW = theme["ACCENT"]
        self.WHITE = theme["WHITE"]
        self.GREY = theme["GREY"]
        self.DARK_GREY = theme["DARK_GREY"]

    # ---------- UI ----------

    def build_ui(self):
        self.root.configure(bg=self.BG)

        header = tk.Frame(self.root, bg=self.BG, height=70)
        header.pack(fill="x", padx=25, pady=(20, 10))
        header.pack_propagate(False)

        tk.Label(
            header, text=APP_NAME, font=("Arial", 25, "bold"),
            fg=self.YELLOW, bg=self.BG
        ).pack(side="left")

        self.theme_button = tk.Button(
            header, text="THEMES", command=self.open_theme_window,
            font=("Arial", 10, "bold"), bg=self.BG, fg=self.YELLOW,
            activebackground=self.PANEL, activeforeground=self.YELLOW,
            relief="flat", bd=0, padx=14, pady=9, cursor="hand2"
        )
        self.theme_button.pack(side="right", padx=(0, 8))

        self.stats_button = tk.Button(
            header, text="STATS", command=self.open_stats_window,
            font=("Arial", 10, "bold"), bg=self.BG, fg=self.YELLOW,
            activebackground=self.PANEL, activeforeground=self.YELLOW,
            relief="flat", bd=0, padx=14, pady=9, cursor="hand2"
        )
        self.stats_button.pack(side="right", padx=(0, 8))

        tk.Button(
            header, text="+ ADD FOLDER", command=self.choose_folder,
            font=("Arial", 10, "bold"), bg=self.YELLOW,
            fg=self.THEMES[self.theme_name]["TEXT_ON_ACCENT"],
            activebackground=self.THEMES[self.theme_name]["ACCENT_ACTIVE"],
            activeforeground=self.THEMES[self.theme_name]["TEXT_ON_ACCENT"],
            bd=0, padx=18, pady=9, cursor="hand2"
        ).pack(side="right")

        main = tk.Frame(self.root, bg=self.BG)
        main.pack(fill="both", expand=True, padx=25, pady=10)

        playlist_card = tk.Frame(main, bg=self.PANEL)
        playlist_card.pack(fill="both", expand=True)

        playlist_header = tk.Frame(playlist_card, bg=self.PANEL, height=55)
        playlist_header.pack(fill="x", padx=18, pady=(12, 5))
        playlist_header.pack_propagate(False)

        tk.Label(
            playlist_header, text="YOUR PLAYLIST",
            font=("Arial", 13, "bold"), fg=self.WHITE, bg=self.PANEL
        ).pack(side="left", padx=(2, 15))

        search_container = tk.Frame(playlist_header, bg=self.DARK_GREY)
        search_container.pack(side="left", fill="x", expand=True, padx=10)

        tk.Label(
            search_container, text="⌕", font=("Arial", 18),
            fg=self.YELLOW, bg=self.DARK_GREY
        ).pack(side="left", padx=(10, 3))

        self.search_entry = tk.Entry(
            search_container, textvariable=self.search_var,
            font=("Arial", 11), fg=self.GREY, bg=self.DARK_GREY,
            insertbackground=self.YELLOW, relief="flat", bd=0
        )
        self.search_entry.pack(side="left", fill="both", expand=True, padx=4)
        self.search_entry.insert(0, "Search songs...")

        tk.Button(
            search_container, text="×", command=self.clear_search,
            font=("Arial", 17, "bold"), fg=self.GREY, bg=self.DARK_GREY,
            activebackground=self.DARK_GREY, activeforeground=self.WHITE,
            relief="flat", bd=0, cursor="hand2"
        ).pack(side="right", padx=7)

        self.search_entry.bind("<FocusIn>", self.search_focus_in)
        self.search_entry.bind("<FocusOut>", self.search_focus_out)
        self.search_var.trace_add("write", self.on_search_changed)

        self.song_count_label = tk.Label(
            playlist_header, text="0 TRACKS", font=("Arial", 9, "bold"),
            fg=self.GREY, bg=self.PANEL
        )
        self.song_count_label.pack(side="right", padx=(10, 2))

        list_frame = tk.Frame(playlist_card, bg=self.PANEL)
        list_frame.pack(fill="both", expand=True, padx=18, pady=(5, 18))

        scrollbar = tk.Scrollbar(
            list_frame, bg=self.PANEL, troughcolor=self.PANEL_2,
            activebackground=self.YELLOW
        )
        scrollbar.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            list_frame, bg=self.PANEL_2, fg=self.WHITE,
            selectbackground=self.YELLOW,
            selectforeground=self.THEMES[self.theme_name]["TEXT_ON_ACCENT"],
            activestyle="none", font=("Arial", 11), bd=0,
            highlightthickness=0, relief="flat", yscrollcommand=scrollbar.set
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)

        self.listbox.bind("<Double-Button-1>", self.play_selected)
        self.listbox.bind("<ButtonPress-1>", self.start_drag)
        self.listbox.bind("<B1-Motion>", self.drag_motion)
        self.listbox.bind("<ButtonRelease-1>", self.end_drag)

        controls = tk.Frame(self.root, bg=self.BG)
        controls.pack(fill="x", padx=25, pady=(5, 20))

        info = tk.Frame(controls, bg=self.BG)
        info.pack(side="left", fill="x", expand=True)

        tk.Label(
            info, textvariable=self.current_title, font=("Arial", 12, "bold"),
            fg=self.WHITE, bg=self.BG, anchor="w"
        ).pack(fill="x")

        tk.Label(
            info, textvariable=self.current_artist, font=("Arial", 9),
            fg=self.GREY, bg=self.BG, anchor="w"
        ).pack(fill="x", pady=(3, 0))

        buttons = tk.Frame(controls, bg=self.BG)
        buttons.pack(side="left", padx=30)

        self.prev_button = self.make_button(buttons, "⏮", self.previous_song, 2)
        self.prev_button.pack(side="left", padx=5)

        self.play_button = tk.Button(
            buttons, text="▶", command=self.toggle_play_pause,
            font=("Arial", 18, "bold"),
            fg=self.THEMES[self.theme_name]["TEXT_ON_ACCENT"],
            bg=self.YELLOW,
            activebackground=self.THEMES[self.theme_name]["ACCENT_ACTIVE"],
            activeforeground=self.THEMES[self.theme_name]["TEXT_ON_ACCENT"],
            relief="flat", bd=0, width=3, cursor="hand2"
        )
        self.play_button.pack(side="left", padx=8)

        self.next_button = self.make_button(buttons, "⏭", self.next_song, 2)
        self.next_button.pack(side="left", padx=5)

        right = tk.Frame(controls, bg=self.BG)
        right.pack(side="right")

        self.shuffle_button = tk.Button(
            right, text="SHUFFLE", command=self.toggle_shuffle,
            font=("Arial", 9, "bold"), fg=self.GREY, bg=self.BG,
            activebackground=self.BG, activeforeground=self.YELLOW,
            relief="flat", bd=0, cursor="hand2"
        )
        self.shuffle_button.pack(side="left", padx=8)

        self.repeat_button = tk.Button(
            right, text="REPEAT", command=self.toggle_repeat,
            font=("Arial", 9, "bold"), fg=self.GREY, bg=self.BG,
            activebackground=self.BG, activeforeground=self.YELLOW,
            relief="flat", bd=0, cursor="hand2"
        )
        self.repeat_button.pack(side="left", padx=8)

        tk.Label(
            right, text="VOL", font=("Arial", 8, "bold"),
            fg=self.GREY, bg=self.BG
        ).pack(side="left", padx=(15, 5))

        self.volume_scale = tk.Scale(
            right, from_=0, to=100, orient="horizontal",
            variable=self.volume_var, command=self.change_volume,
            bg=self.BG, fg=self.WHITE, troughcolor=self.DARK_GREY,
            highlightthickness=0, bd=0, sliderrelief="flat", length=100
        )
        self.volume_scale.pack(side="left")

        progress_frame = tk.Frame(self.root, bg=self.BG)
        progress_frame.pack(fill="x", padx=25, pady=(0, 5))

        self.progress_canvas = tk.Canvas(
            progress_frame, height=8, bg=self.BG,
            highlightthickness=0, bd=0, cursor="hand2"
        )
        self.progress_canvas.pack(fill="x", pady=(5, 3))
        self.progress_canvas.bind("<Button-1>", self.progress_click)
        self.progress_canvas.bind("<B1-Motion>", self.progress_drag)
        self.progress_canvas.bind("<ButtonRelease-1>", self.progress_release)

        tk.Label(
            progress_frame, textvariable=self.time_var, font=("Arial", 8),
            fg=self.GREY, bg=self.BG
        ).pack(anchor="e")

    def make_button(self, parent, text, command, width):
        return tk.Button(
            parent, text=text, command=command,
            font=("Arial", 13, "bold"), fg=self.WHITE, bg=self.PANEL,
            activebackground=self.YELLOW, activeforeground="black",
            relief="flat", bd=0, width=width, cursor="hand2"
        )

    def bind_shortcuts(self):
        self.root.bind("<space>", self.toggle_play_pause)
        self.root.bind("<Control-f>", self.focus_search)
        self.root.bind("<Left>", self.seek_backward)
        self.root.bind("<Right>", self.seek_forward)
        self.root.bind("<Up>", self.previous_song)
        self.root.bind("<Down>", self.next_song)
        self.root.bind("<m>", self.toggle_mute)
        self.root.bind("<M>", self.toggle_mute)
        self.root.bind("<Escape>", self.handle_escape)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- search ----------

    def search_focus_in(self, _event=None):
        if self.search_placeholder_active:
            self.search_placeholder_active = False
            self.search_entry.delete(0, tk.END)
            self.search_entry.configure(fg=self.WHITE)

    def search_focus_out(self, _event=None):
        if not self.search_var.get().strip():
            self.search_placeholder_active = True
            self.search_entry.delete(0, tk.END)
            self.search_entry.insert(0, "Search songs...")
            self.search_entry.configure(fg=self.GREY)

    def clear_search(self):
        self.search_placeholder_active = False
        self.search_entry.delete(0, tk.END)
        self.search_query = ""
        self.refresh_playlist_view()
        self.search_entry.focus_set()

    def on_search_changed(self, *_args):
        if self.search_placeholder_active:
            return
        self.search_query = self.search_var.get().strip().lower()
        self.refresh_playlist_view()

    def focus_search(self, _event=None):
        self.search_entry.focus_set()
        if self.search_placeholder_active:
            self.search_focus_in()
        return "break"

    def get_visible_indexes(self):
        if not self.search_query:
            return list(range(len(self.playlist.songs)))

        return [
            index for index, song in enumerate(self.playlist.songs)
            if self.search_query in f"{song.title} {song.artist} {Path(song.path).name}".lower()
        ]

    def get_real_index_from_selection(self):
        selection = self.listbox.curselection()
        if not selection:
            return None
        visible = selection[0]
        if 0 <= visible < len(self.filtered_indexes):
            return self.filtered_indexes[visible]
        return None

    def refresh_playlist_view(self):
        self.filtered_indexes = self.get_visible_indexes()
        self.listbox.delete(0, tk.END)

        for visible_index, real_index in enumerate(self.filtered_indexes):
            song = self.playlist.songs[real_index]
            self.listbox.insert(
                tk.END,
                f"{visible_index + 1:02d}   {song.title} — {song.artist}"
            )

        self.update_song_count()

        current_index = self.playlist.index_of(self.playlist.current_id)
        if current_index is not None:
            visible = self.filtered_indexes.index(current_index) if current_index in self.filtered_indexes else None
            if visible is not None:
                self.listbox.selection_set(visible)
                self.listbox.activate(visible)
                self.listbox.see(visible)

    def update_song_count(self):
        total = len(self.playlist.songs)
        visible = len(self.filtered_indexes)
        self.song_count_label.config(
            text=f"{visible} / {total} TRACKS" if self.search_query else f"{total} TRACKS"
        )

    # ---------- folders ----------

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return

        self.song_count_label.config(text="SCANNING...")
        threading.Thread(
            target=self.scan_folder,
            args=(folder,),
            daemon=True,
        ).start()

    def scan_folder(self, folder):
        found = []
        try:
            for root_dir, _dirs, files in os.walk(folder):
                for filename in files:
                    if Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS:
                        found.append(os.path.join(root_dir, filename))
        except OSError:
            pass

        found.sort(key=lambda path: os.path.basename(path).lower())
        self.root.after(0, lambda: self.finish_scan(found))

    def finish_scan(self, paths):
        self.playlist.add_paths(paths, self.metadata)
        self.mark_dirty()
        self.refresh_playlist_view()
        self.save_if_dirty()

    # ---------- playback ----------

    def play_selected(self, _event=None):
        index = self.get_real_index_from_selection()
        if index is not None:
            self.play_index(index)

    def play_index(self, index, start=0.0):
        if not (0 <= index < len(self.playlist.songs)):
            return False

        song = self.playlist.songs[index]
        if not self.player.available:
            self.show_audio_warning()
            return False

        if not self.player.load(song.path, song.duration):
            messagebox.showerror(APP_NAME, f"Could not play:\n\n{song.path}")
            return False

        if not self.player.play(start):
            messagebox.showerror(APP_NAME, f"Playback failed:\n\n{self.player.last_error}")
            return False

        self.playlist.current_id = song.id
        self.resume_position = 0.0
        self.stats.record_play(song)
        self.stats_last_tick = time.monotonic()
        self.update_song_info()
        self.refresh_playlist_view()
        self.update_play_button()
        self.mark_dirty()
        self.save_if_dirty()
        return True

    def toggle_play_pause(self, _event=None):
        if self.player.state == PlayerState.PLAYING:
            self.player.pause()
            self.mark_dirty()
        elif self.player.state == PlayerState.PAUSED:
            self.player.resume()
            self.mark_dirty()
        elif self.playlist.current_id:
            self.play_index(self.playlist.index_of(self.playlist.current_id), self.resume_position)
        elif self.playlist.songs:
            self.play_index(0)

        self.update_play_button()
        self.save_if_dirty()
        return "break"

    def update_play_button(self):
        self.play_button.config(text="⏸" if self.player.state == PlayerState.PLAYING else "▶")

    def next_song(self, _event=None):
        if not self.playlist.songs:
            return "break"

        current = self.playlist.index_of(self.playlist.current_id)
        if current is None:
            self.play_index(0)
            return "break"

        if self.repeat_song:
            self.play_index(current)
            return "break"

        if self.shuffle_mode:
            next_id = self.get_next_shuffle_id()
            if next_id:
                self.shuffle_history.append(self.playlist.current_id)
                self.play_index(self.playlist.index_of(next_id))
            return "break"

        next_index = current + 1
        if next_index < len(self.playlist.songs):
            self.play_index(next_index)
        else:
            self.player.stop()
            self.resume_position = 0
            self.update_play_button()
        return "break"

    def previous_song(self, _event=None):
        if not self.playlist.songs or not self.playlist.current_id:
            return "break"

        current = self.playlist.index_of(self.playlist.current_id)
        if current is None:
            return "break"

        if self.player.get_position() > 3:
            self.player.seek(0)
            return "break"

        if self.shuffle_mode and self.shuffle_history:
            previous_id = self.shuffle_history.pop()
            previous_index = self.playlist.index_of(previous_id)
            if previous_index is not None:
                self.play_index(previous_index)
            return "break"

        previous_index = current - 1
        if previous_index < 0:
            previous_index = len(self.playlist.songs) - 1
        self.play_index(previous_index)
        return "break"

    # ---------- shuffle/repeat ----------

    def toggle_shuffle(self):
        self.shuffle_mode = not self.shuffle_mode
        self.shuffle_history.clear()
        self.shuffle_queue.clear()
        if self.shuffle_mode:
            self.build_shuffle_queue()
        self.update_mode_buttons()
        self.mark_dirty()
        self.save_if_dirty()

    def build_shuffle_queue(self):
        current_id = self.playlist.current_id
        ids = [song.id for song in self.playlist.songs if song.id != current_id]
        random.shuffle(ids)
        self.shuffle_queue = ids

    def get_next_shuffle_id(self):
        if not self.shuffle_queue:
            self.build_shuffle_queue()
        return self.shuffle_queue.pop(0) if self.shuffle_queue else None

    def toggle_repeat(self):
        self.repeat_song = not self.repeat_song
        self.update_mode_buttons()
        self.mark_dirty()
        self.save_if_dirty()

    def update_mode_buttons(self):
        self.shuffle_button.config(fg=self.YELLOW if self.shuffle_mode else self.GREY)
        self.repeat_button.config(fg=self.YELLOW if self.repeat_song else self.GREY)

    # ---------- volume ----------

    def toggle_mute(self, _event=None):
        if not self.is_muted:
            current = float(self.volume_var.get())
            if current > 0:
                self.last_volume = current
            self.player.set_volume(0)
            self.volume_var.set(0)
            self.is_muted = True
        else:
            volume = self.last_volume if self.last_volume > 0 else 75
            self.player.set_volume(volume / 100)
            self.volume_var.set(volume)
            self.is_muted = False

        self.mark_dirty()
        self.save_if_dirty()
        return "break"

    def change_volume(self, value):
        try:
            volume = max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            return

        self.player.set_volume(volume / 100)
        if volume > 0:
            self.last_volume = volume
            self.is_muted = False
        else:
            self.is_muted = True

        self.mark_dirty()

    # ---------- progress ----------

    def draw_progress(self, position=None):
        width = self.progress_canvas.winfo_width()
        height = self.progress_canvas.winfo_height()
        if width <= 1:
            return

        duration = self.player.duration
        position = self.player.get_position() if position is None else position
        ratio = 0 if duration <= 0 else max(0, min(1, position / duration))
        played_width = width * ratio

        self.progress_canvas.delete("all")
        self.progress_canvas.create_rectangle(
            0, 0, width, height, fill=self.DARK_GREY, outline=""
        )
        if played_width > 0:
            self.progress_canvas.create_rectangle(
                0, 0, played_width, height, fill=self.YELLOW, outline=""
            )
        self.progress_canvas.create_oval(
            played_width - 5, height / 2 - 5,
            played_width + 5, height / 2 + 5,
            fill=self.YELLOW, outline=""
        )

    def progress_click(self, event):
        self.progress_dragging = True
        self.seek_from_mouse(event.x)

    def progress_drag(self, event):
        self.progress_dragging = True
        self.seek_from_mouse(event.x)

    def progress_release(self, event):
        self.progress_dragging = False
        self.seek_from_mouse(event.x)
        self.mark_dirty()
        self.save_if_dirty()

    def seek_from_mouse(self, x):
        width = self.progress_canvas.winfo_width()
        if width <= 0 or self.player.duration <= 0:
            return

        ratio = max(0, min(1, x / width))
        position = self.player.duration * ratio
        if self.player.seek(position):
            self.resume_position = 0
            self.draw_progress(position)
            self.time_var.set(
                f"{self.format_time(position)} / {self.format_time(self.player.duration)}"
            )

    def seek_backward(self, _event=None):
        if self.player.current_file:
            self.player.seek(max(0, self.player.get_position() - 5))
            self.mark_dirty()
        return "break"

    def seek_forward(self, _event=None):
        if self.player.current_file:
            self.player.seek(min(self.player.duration, self.player.get_position() + 5))
            self.mark_dirty()
        return "break"

    # ---------- drag reorder ----------

    def start_drag(self, event):
        index = self.listbox.nearest(event.y)
        if not (0 <= index < len(self.filtered_indexes)):
            self.drag_start_visible_index = None
            return

        self.drag_start_visible_index = index
        self.drag_start_y = event.y
        self.dragging = False

    def drag_motion(self, event):
        if self.drag_start_visible_index is None:
            return

        if abs(event.y - self.drag_start_y) < 5:
            return

        self.dragging = True
        self.listbox.configure(cursor="fleur")
        target = self.listbox.nearest(event.y)
        if self.filtered_indexes:
            target = max(0, min(target, len(self.filtered_indexes) - 1))
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(target)
            self.listbox.activate(target)
            self.listbox.see(target)

    def end_drag(self, event):
        self.listbox.configure(cursor="")

        if not self.dragging or self.drag_start_visible_index is None:
            self.reset_drag_state()
            return

        if not self.filtered_indexes:
            self.reset_drag_state()
            return

        source_visible = self.drag_start_visible_index
        target_visible = max(
            0,
            min(self.listbox.nearest(event.y), len(self.filtered_indexes) - 1)
        )

        source_real = self.filtered_indexes[source_visible]
        target_real = self.filtered_indexes[target_visible]

        if source_real != target_real:
            self.playlist.move(source_real, target_real)
            self.shuffle_queue = self._preserve_ids(self.shuffle_queue)
            self.shuffle_history = self._preserve_ids(self.shuffle_history)
            self.mark_dirty()
            self.refresh_playlist_view()

        self.reset_drag_state()
        self.save_if_dirty()

    def _preserve_ids(self, ids):
        valid = {song.id for song in self.playlist.songs}
        return [song_id for song_id in ids if song_id in valid]

    def reset_drag_state(self):
        self.drag_start_visible_index = None
        self.drag_start_y = None
        self.dragging = False
        self.listbox.configure(cursor="")

    # ---------- song info ----------

    def update_song_info(self):
        song = self.playlist.get_by_id(self.playlist.current_id)
        if not song:
            self.current_title.set("NO SONG SELECTED")
            self.current_artist.set(APP_NAME)
            return

        self.current_title.set(song.title)
        self.current_artist.set(song.artist)

    # ---------- persistence ----------

    def mark_dirty(self):
        self.state_dirty = True

    def save_if_dirty(self):
        if self.state_dirty:
            self.save_state()

    # ---------- statistics ----------

    @staticmethod
    def format_listening_time(seconds):
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m {secs:02d}s"

    def open_stats_window(self):
        if self.stats_window is not None:
            try:
                if self.stats_window.winfo_exists():
                    self.stats_window.deiconify()
                    self.stats_window.lift()
                    self.refresh_stats_window()
                    return
            except tk.TclError:
                pass

        self.stats_window = tk.Toplevel(self.root)
        self.stats_window.title("Stats")
        self.stats_window.geometry("800x680")
        self.stats_window.minsize(680, 520)
        self.stats_window.configure(bg=self.BG)
        self.stats_window.protocol("WM_DELETE_WINDOW", self.close_stats_window)

        header = tk.Frame(self.stats_window, bg=self.BG)
        header.pack(fill="x", padx=24, pady=(22, 14))
        tk.Label(
            header, text="STATS", font=("Arial", 22, "bold"),
            fg=self.YELLOW, bg=self.BG
        ).pack(side="left")
        tk.Label(
            header, text="WEEKLY LISTENING", font=("Arial", 9, "bold"),
            fg=self.GREY, bg=self.BG
        ).pack(side="right", pady=(8, 0))

        self.stats_body = tk.Frame(self.stats_window, bg=self.BG)
        self.stats_body.pack(fill="both", expand=True, padx=24, pady=(0, 18))

        tk.Button(
            self.stats_window, text="CLOSE", command=self.close_stats_window,
            font=("Arial", 9, "bold"), fg=self.WHITE, bg=self.PANEL_2,
            activebackground=self.YELLOW,
            activeforeground=self.THEMES[self.theme_name]["TEXT_ON_ACCENT"],
            relief="flat", bd=0, padx=24, pady=9, cursor="hand2"
        ).pack(pady=(0, 20))

        self.refresh_stats_window()

    def refresh_stats_window(self):
        if self.stats_window is None:
            return
        try:
            if not self.stats_window.winfo_exists():
                return
        except tk.TclError:
            return

        for widget in self.stats_body.winfo_children():
            widget.destroy()

        rows = self.stats.weekly_rows(12)
        for row in rows:
            card = tk.Frame(self.stats_body, bg=self.PANEL, padx=16, pady=13)
            card.pack(fill="x", pady=5)

            tk.Label(
                card, text=row["label"], font=("Arial", 10, "bold"),
                fg=self.YELLOW, bg=self.PANEL, anchor="w"
            ).pack(fill="x", pady=(0, 9))

            song = row["song"]
            artist = row["artist"]
            song_text = "—" if not song else f"{song.get('title', 'Unknown')}  ({int(song.get('plays', 0))} plays)"
            artist_text = "—" if not artist else f"{artist[0]}  ({artist[1]} plays)"
            time_text = self.format_listening_time(row["seconds"])

            grid = tk.Frame(card, bg=self.PANEL)
            grid.pack(fill="x")
            items = [
                ("MOST PLAYED SONG", song_text),
                ("MOST PLAYED ARTIST", artist_text),
                ("TIME LISTENING TO MUSIC", time_text),
            ]
            for title, value in items:
                block = tk.Frame(grid, bg=self.PANEL)
                block.pack(side="left", fill="x", expand=True, padx=(0, 12))
                tk.Label(
                    block, text=title, font=("Arial", 7, "bold"),
                    fg=self.GREY, bg=self.PANEL, anchor="w"
                ).pack(fill="x")
                tk.Label(
                    block, text=value, font=("Arial", 10, "bold"),
                    fg=self.WHITE, bg=self.PANEL, anchor="w",
                    wraplength=210, justify="left"
                ).pack(fill="x", pady=(3, 0))

    def close_stats_window(self):
        if self.stats_window is not None:
            try:
                self.stats_window.destroy()
            except tk.TclError:
                pass
            self.stats_window = None

    def get_state(self):
        position = self.player.get_position()
        if self.player.state == PlayerState.STOPPED and self.resume_position > 0:
            position = self.resume_position

        return {
            "songs": [asdict(song) for song in self.playlist.songs],
            "current_id": self.playlist.current_id,
            "position": max(0.0, float(position)),
            "volume": float(self.volume_var.get()),
            "last_volume": float(self.last_volume),
            "muted": bool(self.is_muted),
            "shuffle": bool(self.shuffle_mode),
            "repeat": bool(self.repeat_song),
            "shuffle_queue": list(self.shuffle_queue),
            "shuffle_history": list(self.shuffle_history),
            "search": "" if self.search_placeholder_active else self.search_var.get(),
            "fullscreen": bool(self.root.attributes("-fullscreen")),
            "geometry": self.root.geometry(),
            "theme": self.theme_name,
            "stats": self.stats.export(),
        }

    def save_state(self):
        try:
            self.state_manager.save(self.get_state())
            self.state_dirty = False
        except (OSError, TypeError, ValueError) as exc:
            print(f"Save error: {exc}")

    def load_state(self):
        data = self.state_manager.load()
        self.stats.import_data(data.get("stats", {}))
        self.stats_last_tick = time.monotonic()

        saved_songs = data.get("songs", [])
        loaded = []

        if isinstance(saved_songs, list):
            for item in saved_songs:
                if not isinstance(item, dict):
                    continue

                path = item.get("path")
                if not isinstance(path, str) or not os.path.isfile(path):
                    continue

                info = self.metadata.get(path)
                song = Song(
                    id=str(item.get("id") or os.urandom(8).hex()),
                    path=os.path.abspath(path),
                    title=str(item.get("title") or info["title"]),
                    artist=str(item.get("artist") or info["artist"]),
                    duration=float(item.get("duration") or info["duration"]),
                )
                loaded.append(song)

        self.playlist.songs = loaded

        current_id = data.get("current_id")
        if current_id in {song.id for song in loaded}:
            self.playlist.current_id = current_id
        else:
            # Backward compatibility with the previous index-based save format.
            old_index = data.get("current_index")
            if isinstance(old_index, int) and 0 <= old_index < len(loaded):
                self.playlist.current_id = loaded[old_index].id

        try:
            volume = max(0.0, min(100.0, float(data.get("volume", 75))))
        except (TypeError, ValueError):
            volume = 75.0

        try:
            self.last_volume = max(0.0, min(100.0, float(data.get("last_volume", 75))))
        except (TypeError, ValueError):
            self.last_volume = 75.0

        self.volume_var.set(volume)
        self.is_muted = bool(data.get("muted", False))
        self.player.set_volume(0 if self.is_muted else volume / 100)

        self.shuffle_mode = bool(data.get("shuffle", False))
        self.repeat_song = bool(data.get("repeat", False))

        valid_ids = {song.id for song in loaded}
        self.shuffle_queue = [
            value for value in data.get("shuffle_queue", [])
            if value in valid_ids
        ]
        self.shuffle_history = [
            value for value in data.get("shuffle_history", [])
            if value in valid_ids
        ]

        # If an old save used numeric shuffle state, rebuild safely.
        if self.shuffle_mode and not self.shuffle_queue and loaded:
            self.build_shuffle_queue()

        try:
            self.resume_position = max(0.0, float(data.get("position", 0)))
        except (TypeError, ValueError):
            self.resume_position = 0.0

        theme = data.get("theme", "Honey")
        if theme in self.THEMES:
            self.apply_theme(theme, save=False)

        geometry = data.get("geometry")
        if isinstance(geometry, str) and geometry:
            try:
                self.root.geometry(geometry)
            except tk.TclError:
                pass

        saved_search = str(data.get("search", "")).strip()
        if saved_search:
            self.search_placeholder_active = False
            self.search_entry.delete(0, tk.END)
            self.search_entry.insert(0, saved_search)
            self.search_entry.configure(fg=self.WHITE)
            self.search_query = saved_search.lower()

        if self.playlist.current_id:
            song = self.playlist.get_by_id(self.playlist.current_id)
            if song and self.player.load(song.path, song.duration):
                self.resume_position = min(self.resume_position, self.player.duration)

        try:
            self.root.attributes("-fullscreen", bool(data.get("fullscreen", False)))
        except tk.TclError:
            pass

        self.update_mode_buttons()
        self.refresh_playlist_view()
        self.update_song_info()
        self.update_play_button()
        self.draw_progress(self.resume_position)

        if self.player.duration:
            self.time_var.set(
                f"{self.format_time(self.resume_position)} / {self.format_time(self.player.duration)}"
            )

        self.state_dirty = False

    def autosave_loop(self):
        if self.closed:
            return
        self.save_if_dirty()
        self.root.after(15000, self.autosave_loop)

    # ---------- themes ----------

    def open_theme_window(self):
        if hasattr(self, "theme_window") and self.theme_window.winfo_exists():
            self.theme_window.lift()
            self.theme_window.focus_force()
            return

        colors = self.THEMES[self.theme_name]
        self.theme_window = tk.Toplevel(self.root)
        self.theme_window.title(f"{APP_NAME} • THEMES")
        self.theme_window.geometry("460x560")
        self.theme_window.resizable(False, False)
        self.theme_window.configure(bg=colors["BG"])
        self.theme_window.transient(self.root)
        self.theme_window.grab_set()

        tk.Label(
            self.theme_window, text="CHOOSE YOUR THEME",
            font=("Arial", 16, "bold"),
            fg=colors["WHITE"], bg=colors["BG"]
        ).pack(pady=(22, 5))

        tk.Label(
            self.theme_window, text="Your choice changes PLAY HONEY instantly",
            font=("Arial", 9), fg=colors["GREY"], bg=colors["BG"]
        ).pack(pady=(0, 18))

        for name, theme in self.THEMES.items():
            card = tk.Frame(
                self.theme_window, bg=theme["PANEL"], height=50,
                cursor="hand2", highlightthickness=1,
                highlightbackground=theme["DARK_GREY"]
            )
            card.pack(fill="x", padx=28, pady=5)
            card.pack_propagate(False)

            swatch = tk.Label(card, text="  ", bg=theme["ACCENT"], width=4)
            swatch.pack(side="left", padx=(10, 8), pady=9)

            label = tk.Label(
                card, text=name, font=("Arial", 11, "bold"),
                fg=theme["WHITE"], bg=theme["PANEL"], anchor="w"
            )
            label.pack(side="left", fill="x", expand=True)

            check = tk.Label(
                card, text="✓" if name == self.theme_name else "",
                font=("Arial", 15, "bold"),
                fg=theme["ACCENT"], bg=theme["PANEL"], width=3
            )
            check.pack(side="right", padx=8)

            for widget in (card, swatch, label, check):
                widget.bind(
                    "<Button-1>",
                    lambda _event, selected=name: self.select_theme(selected)
                )

        tk.Button(
            self.theme_window, text="CLOSE",
            command=self.close_theme_window,
            font=("Arial", 9, "bold"),
            fg=colors["WHITE"], bg=colors["PANEL_2"],
            activebackground=colors["ACCENT"],
            activeforeground=colors["TEXT_ON_ACCENT"],
            relief="flat", bd=0, padx=22, pady=8, cursor="hand2"
        ).pack(pady=(15, 18))

    def select_theme(self, name):
        if name in self.THEMES:
            self.apply_theme(name, save=True)
            if hasattr(self, "theme_window") and self.theme_window.winfo_exists():
                self.theme_window.grab_release()
                self.theme_window.destroy()

    def close_theme_window(self):
        if hasattr(self, "theme_window") and self.theme_window.winfo_exists():
            self.theme_window.grab_release()
            self.theme_window.destroy()

    def apply_theme(self, name, save=True):
        if name not in self.THEMES:
            return

        self.apply_theme_values(name)
        theme = self.THEMES[name]
        self.root.configure(bg=self.BG)

        def recolor(parent):
            for widget in parent.winfo_children():
                try:
                    if isinstance(widget, tk.Listbox):
                        widget.configure(
                            bg=self.PANEL_2, fg=self.WHITE,
                            selectbackground=self.YELLOW,
                            selectforeground=theme["TEXT_ON_ACCENT"]
                        )
                    elif isinstance(widget, tk.Entry):
                        widget.configure(
                            bg=self.DARK_GREY,
                            fg=self.GREY if self.search_placeholder_active else self.WHITE,
                            insertbackground=self.YELLOW
                        )
                    elif isinstance(widget, tk.Scrollbar):
                        widget.configure(
                            bg=self.PANEL, troughcolor=self.PANEL_2,
                            activebackground=self.YELLOW
                        )
                    elif isinstance(widget, tk.Canvas):
                        widget.configure(bg=self.BG)
                    elif isinstance(widget, tk.Scale):
                        widget.configure(
                            bg=self.BG, fg=self.WHITE,
                            troughcolor=self.DARK_GREY,
                            activebackground=self.YELLOW
                        )
                    else:
                        self._recolor_widget(widget, theme)
                except tk.TclError:
                    pass
                recolor(widget)

        recolor(self.root)

        self.play_button.configure(
            bg=self.YELLOW,
            fg=theme["TEXT_ON_ACCENT"],
            activebackground=theme["ACCENT_ACTIVE"],
            activeforeground=theme["TEXT_ON_ACCENT"]
        )
        self.update_mode_buttons()
        self.theme_button.configure(
            bg=self.BG, fg=self.YELLOW,
            activebackground=self.PANEL, activeforeground=self.YELLOW
        )
        self.draw_progress()
        self.update_current_ui()

        if save:
            self.mark_dirty()
            self.save_if_dirty()

    def _recolor_widget(self, widget, theme):
        # Only recolor widgets that belong to known PLAY HONEY roles.
        role_map = {}
        for role, values in {
            "BG": {item["BG"] for item in self.THEMES.values()},
            "PANEL": {item["PANEL"] for item in self.THEMES.values()},
            "PANEL_2": {item["PANEL_2"] for item in self.THEMES.values()},
            "ACCENT": {item["ACCENT"] for item in self.THEMES.values()},
            "WHITE": {item["WHITE"] for item in self.THEMES.values()},
            "GREY": {item["GREY"] for item in self.THEMES.values()},
            "DARK_GREY": {item["DARK_GREY"] for item in self.THEMES.values()},
        }.items():
            for value in values:
                role_map[value] = role

        try:
            bg = widget.cget("bg")
            fg = widget.cget("fg")

            if bg in role_map:
                widget.configure(bg=theme[role_map[bg]])
            if fg in role_map:
                widget.configure(fg=theme[role_map[fg]])

            if isinstance(widget, tk.Button):
                active_bg = widget.cget("activebackground")
                active_fg = widget.cget("activeforeground")
                if active_bg in role_map:
                    widget.configure(activebackground=theme[role_map[active_bg]])
                if active_fg in role_map:
                    widget.configure(activeforeground=theme[role_map[active_fg]])
        except tk.TclError:
            pass

    def update_current_ui(self):
        self.update_song_info()
        current_index = self.playlist.index_of(self.playlist.current_id)
        if current_index is None:
            return

        visible = (
            self.filtered_indexes.index(current_index)
            if current_index in self.filtered_indexes
            else None
        )
        if visible is not None:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(visible)
            self.listbox.activate(visible)

    # ---------- update loop ----------

    def update_loop(self):
        if self.closed:
            return

        now = time.monotonic()
        elapsed = max(0.0, min(1.0, now - self.stats_last_tick))
        self.stats_last_tick = now
        if self.player.state == PlayerState.PLAYING and self.playlist.current_id:
            self.stats.add_listening_time(elapsed)
            if elapsed > 0:
                self.mark_dirty()

        try:
            if self.player.state in (PlayerState.PLAYING, PlayerState.PAUSED):
                position = self.player.get_position()
                duration = self.player.duration

                if duration > 0:
                    if not self.progress_dragging:
                        self.draw_progress(position)
                    self.time_var.set(
                        f"{self.format_time(position)} / {self.format_time(duration)}"
                    )

                if self.player.is_finished():
                    self.handle_track_finished()
            elif not self.progress_dragging:
                self.draw_progress(self.resume_position)
                if self.player.duration:
                    self.time_var.set(
                        f"{self.format_time(self.resume_position)} / {self.format_time(self.player.duration)}"
                    )

            self.update_play_button()
        except (tk.TclError, pygame.error):
            pass

        self.root.after(250, self.update_loop)

    def handle_track_finished(self):
        if self.repeat_song:
            current = self.playlist.index_of(self.playlist.current_id)
            if current is not None:
                self.play_index(current)
            return

        if self.shuffle_mode:
            next_id = self.get_next_shuffle_id()
            if next_id:
                if self.playlist.current_id:
                    self.shuffle_history.append(self.playlist.current_id)
                index = self.playlist.index_of(next_id)
                if index is not None:
                    self.play_index(index)
            else:
                self.player.stop()
            return

        current = self.playlist.index_of(self.playlist.current_id)
        if current is None:
            return

        next_index = current + 1
        if next_index < len(self.playlist.songs):
            self.play_index(next_index)
        else:
            self.player.stop()
            self.resume_position = 0
            self.draw_progress(0)
            self.update_play_button()

    # ---------- misc ----------

    @staticmethod
    def format_time(seconds):
        try:
            total = max(0, int(seconds))
        except (TypeError, ValueError):
            total = 0
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    def show_audio_warning(self):
        if self.player.available:
            return
        messagebox.showwarning(
            APP_NAME,
            "Audio engine could not be initialized.\n\n"
            "Install/enable a working audio device and pygame mixer support."
        )

    def handle_escape(self, _event=None):
        try:
            if self.root.attributes("-fullscreen"):
                self.root.attributes("-fullscreen", False)
                self.mark_dirty()
                self.save_if_dirty()
                return "break"
        except tk.TclError:
            pass

        if not self.search_placeholder_active and self.search_var.get().strip():
            self.clear_search()
        return "break"

    def toggle_fullscreen(self, _event=None):
        try:
            current = bool(self.root.attributes("-fullscreen"))
            self.root.attributes("-fullscreen", not current)
            self.mark_dirty()
            self.save_if_dirty()
        except tk.TclError:
            pass
        return "break"

    def on_close(self):
        if self.closed:
            return
        self.closed = True

        self.save_state()
        self.close_stats_window()
        self.player.cleanup()

        try:
            self.root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = MusicPlayer(root)
    root.mainloop()
