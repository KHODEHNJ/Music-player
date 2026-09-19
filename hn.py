import tkinter as tk
from tkinter import filedialog, messagebox
import pygame
import os
from mutagen.mp3 import MP3


class MusicPlayer:

    def __init__(self, root):
        self.root = root
        self.root.title("PLAY HONEY")
        self.root.geometry("500x560")
        self.root.configure(bg="#000000")
        self.root.resizable(False, False)

        try:
            pygame.mixer.pre_init(
                frequency=44100,
                size=-16,
                channels=2,
                buffer=512
            )

            pygame.init()
            pygame.mixer.music.set_volume(1.0)

        except Exception as e:
            messagebox.showerror(
                "Pygame Error",
                str(e)
            )
            return

        self.playlist = []
        self.current_index = 0

        self.is_playing = False
        self.is_paused = False

        self.song_length = 0
        self.song_position = 0
        self.play_start_position = 0

        self.user_seeking = False
        self.repeat_song = False

        self.create_widgets()

    def create_widgets(self):

        title = tk.Label(
            self.root,
            text="PLAY HONEY",
            font=("Arial", 20, "bold"),
            bg="#000000",
            fg="#ffffff"
        )

        title.pack(pady=(15, 8))

        top_bar = tk.Frame(
            self.root,
            bg="#000000"
        )

        top_bar.pack(pady=3)

        button_style = {
            "font": ("Arial", 9, "bold"),
            "bg": "#111111",
            "fg": "#ffffff",
            "activebackground": "#ffffff",
            "activeforeground": "#000000",
            "bd": 1,
            "relief": "solid",
            "highlightthickness": 0,
            "padx": 7,
            "pady": 3,
            "cursor": "hand2"
        }

        tk.Button(
            top_bar,
            text="📁 Add Folder",
            command=self.add_folder,
            **button_style
        ).grid(
            row=0,
            column=0,
            padx=2
        )

        tk.Button(
            top_bar,
            text="🗑 Delete",
            command=self.remove_song,
            **button_style
        ).grid(
            row=0,
            column=1,
            padx=2
        )

        tk.Button(
            top_bar,
            text="🧹 Clear",
            command=self.clear_playlist,
            **button_style
        ).grid(
            row=0,
            column=2,
            padx=2
        )

        self.listbox = tk.Listbox(
            self.root,
            bg="#111111",
            fg="#ffffff",
            selectbackground="#ffffff",
            selectforeground="#000000",
            font=("Arial", 11),
            height=11,
            bd=1,
            relief="solid",
            highlightthickness=0,
            activestyle="none"
        )

        self.listbox.pack(
            fill="both",
            padx=20,
            pady=10
        )

        self.listbox.bind(
            "<Double-Button-1>",
            self.play_selected
        )

        self.current_song = tk.Label(
            self.root,
            text="No song",
            font=("Arial", 12),
            bg="#000000",
            fg="#ffffff"
        )

        self.current_song.pack(pady=5)

        self.time_label = tk.Label(
            self.root,
            text="00:00 / 00:00",
            font=("Arial", 10),
            bg="#000000",
            fg="#cccccc"
        )

        self.time_label.pack()

        self.progress = tk.Scale(
            self.root,
            from_=0,
            to=100,
            orient="horizontal",
            showvalue=False,
            length=420,
            resolution=0.1,
            bg="#000000",
            fg="#ffffff",
            troughcolor="#222222",
            activebackground="#ffffff",
            highlightthickness=0,
            bd=0,
            sliderrelief="flat",
            sliderlength=18
        )

        self.progress.pack(pady=5)

        self.progress.bind(
            "<Button-1>",
            self.start_seek
        )

        self.progress.bind(
            "<ButtonRelease-1>",
            self.finish_seek
        )

        frame = tk.Frame(
            self.root,
            bg="#000000"
        )

        frame.pack(pady=10)

        btn_style = {
            "font": ("Arial", 11, "bold"),
            "bg": "#111111",
            "fg": "#ffffff",
            "activebackground": "#ffffff",
            "activeforeground": "#000000",
            "bd": 1,
            "relief": "solid",
            "highlightthickness": 0,
            "width": 5,
            "cursor": "hand2"
        }

        tk.Button(
            frame,
            text="⏪ 10s",
            command=self.backward_10,
            **btn_style
        ).grid(
            row=0,
            column=0,
            padx=2
        )

        tk.Button(
            frame,
            text="⏮",
            command=self.prev_song,
            **btn_style
        ).grid(
            row=0,
            column=1,
            padx=2
        )

        self.play_btn = tk.Button(
            frame,
            text="▶",
            command=self.toggle_play,
            **btn_style
        )

        self.play_btn.grid(
            row=0,
            column=2,
            padx=2
        )

        tk.Button(
            frame,
            text="⏭",
            command=self.next_song,
            **btn_style
        ).grid(
            row=0,
            column=3,
            padx=2
        )

        tk.Button(
            frame,
            text="10s ⏩",
            command=self.forward_10,
            **btn_style
        ).grid(
            row=0,
            column=4,
            padx=2
        )

        self.repeat_btn = tk.Button(
            self.root,
            text="🔁",
            command=self.toggle_repeat,
            font=("Arial", 15, "bold"),
            bg="#111111",
            fg="#ffffff",
            activebackground="#ffffff",
            activeforeground="#000000",
            bd=1,
            relief="solid",
            highlightthickness=0,
            width=5,
            cursor="hand2"
        )

        self.repeat_btn.pack(
            pady=(0, 5)
        )

        self.update_time()

    def add_folder(self):

        folder = filedialog.askdirectory(
            title="Select Music Folder"
        )

        if not folder:
            return

        supported = (
            ".mp3",
            ".wav",
            ".ogg"
        )

        new_songs = []

        for root_dir, dirs, files in os.walk(folder):

            for file in files:

                if file.lower().endswith(supported):

                    full_path = os.path.join(
                        root_dir,
                        file
                    )

                    new_songs.append(full_path)

        new_songs.sort(
            key=lambda x: os.path.basename(x).lower()
        )

        added = 0

        for song in new_songs:

            if song not in self.playlist:

                self.playlist.append(song)

                self.listbox.insert(
                    tk.END,
                    os.path.basename(song)
                )

                added += 1

        if added == 0:

            messagebox.showinfo(
                "Playlist",
                "No new songs found."
            )

        else:

            messagebox.showinfo(
                "Playlist",
                f"{added} songs added."
            )

    def remove_song(self):

        selection = self.listbox.curselection()

        if not selection:

            messagebox.showwarning(
                "Warning",
                "Please select a song."
            )

            return

        index = selection[0]

        if index == self.current_index:

            pygame.mixer.music.stop()

            self.is_playing = False
            self.is_paused = False

            self.song_position = 0
            self.song_length = 0
            self.play_start_position = 0

            self.play_btn.config(
                text="▶"
            )

            self.current_song.config(
                text="No song"
            )

            self.time_label.config(
                text="00:00 / 00:00"
            )

            self.progress.set(0)

        self.listbox.delete(index)
        self.playlist.pop(index)

        if not self.playlist:

            self.current_index = 0

        elif self.current_index >= len(self.playlist):

            self.current_index = len(self.playlist) - 1

    def clear_playlist(self):

        if not self.playlist:
            return

        answer = messagebox.askyesno(
            "Clear Playlist",
            "Are you sure you want to clear the entire playlist?"
        )

        if not answer:
            return

        pygame.mixer.music.stop()

        self.playlist.clear()

        self.listbox.delete(
            0,
            tk.END
        )

        self.current_index = 0

        self.is_playing = False
        self.is_paused = False

        self.song_length = 0
        self.song_position = 0
        self.play_start_position = 0

        self.current_song.config(
            text="No song"
        )

        self.time_label.config(
            text="00:00 / 00:00"
        )

        self.progress.set(0)

        self.play_btn.config(
            text="▶"
        )

    def play_selected(self, event=None):

        selection = self.listbox.curselection()

        if not selection:
            return

        self.current_index = selection[0]

        self.play_song()

    def play_song(self):

        if not self.playlist:
            return

        file_path = self.playlist[
            self.current_index
        ]

        if not os.path.exists(file_path):

            messagebox.showerror(
                "Error",
                "File not found:\n\n" + file_path
            )

            return

        try:

            pygame.mixer.music.stop()

            pygame.mixer.music.load(
                file_path
            )

            pygame.mixer.music.set_volume(
                1.0
            )

            pygame.mixer.music.play()

            self.is_playing = True
            self.is_paused = False

            self.song_position = 0
            self.play_start_position = 0

            self.play_btn.config(
                text="⏸"
            )

            self.current_song.config(
                text="🎵 " + os.path.basename(file_path)
            )

            try:

                if file_path.lower().endswith(".mp3"):

                    audio = MP3(file_path)

                    self.song_length = audio.info.length

                else:

                    sound = pygame.mixer.Sound(file_path)

                    self.song_length = sound.get_length()

            except Exception:

                self.song_length = 0

            self.progress.set(0)

            self.time_label.config(
                text=(
                    "00:00 / "
                    + self.format_time(
                        self.song_length
                    )
                )
            )

            self.listbox.selection_clear(
                0,
                tk.END
            )

            self.listbox.selection_set(
                self.current_index
            )

            self.listbox.activate(
                self.current_index
            )

        except Exception as e:

            messagebox.showerror(
                "Playback Error",
                str(e)
            )

    def toggle_play(self):

        if not self.playlist:
            return

        if not self.is_playing:

            self.play_song()

        elif self.is_paused:

            pygame.mixer.music.unpause()

            self.is_paused = False

            self.play_btn.config(
                text="⏸"
            )

        else:

            current = (
                pygame.mixer.music.get_pos()
                / 1000
            )

            self.song_position = (
                self.play_start_position
                + current
            )

            pygame.mixer.music.pause()

            self.is_paused = True

            self.play_btn.config(
                text="▶"
            )

    def toggle_repeat(self):

        self.repeat_song = not self.repeat_song

        if self.repeat_song:

            self.repeat_btn.config(
                bg="#ffffff",
                fg="#000000"
            )

        else:

            self.repeat_btn.config(
                bg="#111111",
                fg="#ffffff"
            )

    def next_song(self):

        if not self.playlist:
            return

        self.current_index = (
            self.current_index + 1
        ) % len(self.playlist)

        self.play_song()

    def prev_song(self):

        if not self.playlist:
            return

        self.current_index = (
            self.current_index - 1
        ) % len(self.playlist)

        self.play_song()

    def get_current_position(self):

        if self.is_paused:

            return self.song_position

        pygame_position = (
            pygame.mixer.music.get_pos()
            / 1000
        )

        if pygame_position < 0:
            pygame_position = 0

        current = (
            self.play_start_position
            + pygame_position
        )

        return min(
            current,
            self.song_length
        )

    def seek_to(self, position):

        if self.song_length <= 0:
            return

        position = max(
            0,
            min(
                position,
                self.song_length
            )
        )

        was_paused = self.is_paused

        try:

            pygame.mixer.music.play(
                start=position
            )

            self.play_start_position = position
            self.song_position = position

            if was_paused:

                pygame.mixer.music.pause()

                self.is_paused = True

            else:

                self.is_paused = False

                self.play_btn.config(
                    text="⏸"
                )

            percent = (
                position /
                self.song_length
            ) * 100

            self.progress.set(
                percent
            )

            self.time_label.config(
                text=(
                    self.format_time(position)
                    + " / "
                    + self.format_time(
                        self.song_length
                    )
                )
            )

        except Exception as e:

            print(
                "Seek error:",
                e
            )

    def backward_10(self):

        if not self.playlist:
            return

        if self.song_length <= 0:
            return

        try:

            current = self.get_current_position()

            new_position = max(
                0,
                current - 10
            )

            self.seek_to(
                new_position
            )

        except Exception as e:

            print(
                "Backward error:",
                e
            )

    def forward_10(self):

        if not self.playlist:
            return

        if self.song_length <= 0:
            return

        try:

            current = self.get_current_position()

            new_position = min(
                self.song_length,
                current + 10
            )

            self.seek_to(
                new_position
            )

        except Exception as e:

            print(
                "Forward error:",
                e
            )

    def start_seek(self, event):

        if self.is_playing:

            self.user_seeking = True

    def finish_seek(self, event):

        if not self.is_playing:
            return

        if self.song_length <= 0:
            return

        try:

            percent = self.progress.get()

            new_position = (
                percent / 100
            ) * self.song_length

            self.seek_to(
                new_position
            )

        except Exception as e:

            print(
                "Seek error:",
                e
            )

        finally:

            self.user_seeking = False

    def format_time(self, seconds):

        seconds = int(
            max(0, seconds)
        )

        minutes = seconds // 60
        seconds = seconds % 60

        return (
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    def update_time(self):

        if (
            self.is_playing
            and not self.is_paused
            and not self.user_seeking
            and self.song_length > 0
        ):

            pygame_position = (
                pygame.mixer.music.get_pos()
                / 1000
            )

            if pygame_position < 0:
                pygame_position = 0

            current = (
                self.play_start_position
                + pygame_position
            )

            if current > self.song_length:
                current = self.song_length

            self.song_position = current

            percent = (
                current /
                self.song_length
            ) * 100

            self.progress.set(
                percent
            )

            self.time_label.config(
                text=(
                    self.format_time(current)
                    + " / "
                    + self.format_time(
                        self.song_length
                    )
                )
            )

            if not pygame.mixer.music.get_busy():

                if self.repeat_song:

                    self.play_song()

                else:

                    self.next_song()

        self.root.after(
            300,
            self.update_time
        )


if __name__ == "__main__":

    root = tk.Tk()

    app = MusicPlayer(root)

    root.mainloop()
