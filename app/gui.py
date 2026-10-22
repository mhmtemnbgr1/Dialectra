"""Dialectra masaüstü arayüzü (tkinter, Windows 95 görünümü).

Çeviri motoru (asyncio) ayrı bir iş parçacığında çalışır; arayüzle bir kuyruk
üzerinden haberleşir. tkinter'a yalnızca ana iş parçacığından dokunulur.

Kabartmalı / gömük kenarları tkinter kendisi çizer (relief="raised"/"sunken");
gri zemin üzerinde Windows 95'teki 3B görünümü verir.
"""

import asyncio
import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import ttk

import sounddevice as sd

from app import settings
from app import i18n
from app.hotkey import GlobalHotkey
from app.i18n import t
from app.live_translate import FatalError, LiveTranslator, list_devices
from app.system_audio import AudioDeviceError

C = {
    "face": "#C0C0C0",  # pencere ve düğme grisi
    "light": "#DFDFDF",
    "shadow": "#808080",
    "window": "#FFFFFF",  # içerik alanı
    "text": "#000000",
    "muted": "#606060",
    "navy": "#000080",
    "title_end": "#1084D0",  # başlık çubuğu geçişinin sağ ucu
    "tooltip": "#FFFFE1",
    "en": "#000080",
    "tr": "#800000",
    "live": "#008000",
    "warn": "#808000",
    "error": "#C00000",
    "idle": "#808080",
}
STATUS_COLORS = {"idle": C["idle"], "connecting": C["warn"], "reconnecting": C["warn"],
                 "live": C["live"], "error": C["error"]}
FONTS = ("Microsoft Sans Serif", "Tahoma")  # ilk bulunan kullanılır
SIGNAL_LEVEL = 0.0005  # bunun altı "hiç sinyal yok" (ortam gürültüsü bile değil)
MIC_SILENT_AFTER = 4.0  # sn
API_KEY_URL = "https://aistudio.google.com/apikey"
SOURCES = ("mic", "system")
DIRECTIONS = {"both": None, "en-tr": "EN → TR", "tr-en": "TR → EN"}  # None: çevrilen metin
HOTKEY = "D"  # Ctrl+Alt+D: başlat / durdur
HOTKEY_LABEL = "Ctrl+Alt+D"
METER_BLOCKS = 14

_scale = 1.0
_family = FONTS[-1]


def px(n: float) -> int:
    """96 DPI'ye göre verilmiş pikseli ekran ölçeğine çevirir."""
    return round(n * _scale)


def font(size: int, weight: str = "normal") -> tuple:
    return (_family, size, weight)


def create_root() -> tk.Tk:
    global _scale, _family
    if sys.platform == "win32":
        try:  # bulanık görünmesin diye ekran ölçeğini kendimiz uygularız
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    _scale = root.winfo_fpixels("1i") / 96
    families = set(tkfont.families(root))
    _family = next((f for f in FONTS if f in families), FONTS[-1])
    root.option_add("*Font", font(9))
    style = ttk.Style(root)
    style.theme_use("classic")  # yalnızca kaydırma çubuğu ttk; kabartmalı oklar
    style.configure("Vertical.TScrollbar", background=C["face"], troughcolor=C["light"],
                    arrowcolor=C["text"], borderwidth=2, arrowsize=px(16))
    return root


def button(parent, text, command, **kw) -> tk.Button:
    opts = dict(bg=C["face"], activebackground=C["face"], fg=C["text"], relief="raised", bd=2,
                padx=px(10), pady=px(2), font=font(9), disabledforeground=C["shadow"])
    opts.update(kw)
    return tk.Button(parent, text=text, command=command, **opts)


def group(parent, title: str) -> tk.LabelFrame:
    return tk.LabelFrame(parent, text=f" {title} ", bg=C["face"], fg=C["text"], bd=2,
                         relief="groove", padx=px(8), pady=px(4), font=font(9))


def choice(parent, cls, **kw):
    """Gri zemine uygun Radiobutton / Checkbutton."""
    return cls(parent, bg=C["face"], activebackground=C["face"], fg=C["text"],
               selectcolor=C["window"], disabledforeground=C["shadow"], highlightthickness=0,
               font=font(9), **kw)


class TitleBar(tk.Canvas):
    """Windows 98'deki gibi lacivertten maviye geçişli başlık şeridi."""

    def __init__(self, parent, title: str, subtitle: str = "", height: int = 30) -> None:
        super().__init__(parent, height=px(height), highlightthickness=0, bd=0, bg=C["navy"])
        self.title, self.subtitle = title, subtitle
        self.bind("<Configure>", self._draw)

    def _draw(self, event=None) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        start = [int(C["navy"][i:i + 2], 16) for i in (1, 3, 5)]
        end = [int(C["title_end"][i:i + 2], 16) for i in (1, 3, 5)]
        steps = 64
        for i in range(steps):
            rgb = [round(a + (b - a) * i / (steps - 1)) for a, b in zip(start, end)]
            x0, x1 = w * i // steps, w * (i + 1) // steps + 1
            self.create_rectangle(x0, 0, x1, h, outline="", fill="#%02x%02x%02x" % tuple(rgb))
        self.create_text(px(8), h // 2, anchor="w", fill="white", font=font(10, "bold"),
                         text=self.title)
        if self.subtitle:
            title_w = tkfont.Font(font=font(10, "bold")).measure(self.title)
            self.create_text(px(20) + title_w, h // 2, anchor="w", fill="#DDE6FF", font=font(9),
                             text=self.subtitle)


class Overlay(tk.Toplevel):
    """Her zaman üstte duran, sürüklenebilir altyazı penceresi (sarı ipucu kutusu).

    Sürükleyerek taşınır, fare tekerleğiyle yazı boyutu değişir. Konumu ve
    boyutu `prefs` sözlüğüne yazılır.
    """

    WIDTH = 760

    def __init__(self, master, prefs: dict, on_close) -> None:
        super().__init__(master, bg=C["text"])
        self.prefs = prefs
        self.on_close = on_close
        self.size = prefs.get("size", 18)
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        frame = tk.Frame(self, bg=C["face"], bd=2, relief="raised")
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        bar = tk.Frame(frame, bg=C["navy"])
        bar.pack(fill="x", padx=px(2), pady=(px(2), 0))
        title = tk.Label(bar, text=t("overlay_title"), bg=C["navy"], fg="white", font=font(9, "bold"))
        title.pack(side="left", padx=px(4))
        button(bar, "×", self.on_close, padx=px(4), pady=0, font=font(9, "bold")).pack(
            side="right", padx=px(2), pady=px(2)
        )
        body = tk.Frame(frame, bg=C["tooltip"], bd=1, relief="solid")
        body.pack(fill="both", expand=True, padx=px(4), pady=px(4))
        tk.Frame(body, width=px(self.WIDTH), height=0, bg=C["tooltip"]).pack()  # sabit genişlik
        wrap = px(self.WIDTH - 30)
        self.heard = tk.Label(body, text="", bg=C["tooltip"], fg=C["muted"], wraplength=wrap,
                              justify="center")
        self.heard.pack(fill="x", padx=px(12), pady=(px(6), 0))
        self.said = tk.Label(body, text=t("overlay_empty"), bg=C["tooltip"],
                             fg=C["text"], wraplength=wrap, justify="center")
        self.said.pack(fill="x", padx=px(12), pady=(px(2), px(10)))
        self._apply_size()

        for widget in (self, bar, title, body, self.heard, self.said):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<ButtonRelease-1>", self._save_position)
            widget.bind("<MouseWheel>", self._resize_text)

        self.update_idletasks()
        x = prefs.get("x", (self.winfo_screenwidth() - self.winfo_width()) // 2)
        y = prefs.get("y", self.winfo_screenheight() - self.winfo_height() - px(120))
        self.geometry(f"+{x}+{y}")

    def _apply_size(self) -> None:
        self.heard.configure(font=font(max(8, round(self.size * 0.6))))
        self.said.configure(font=font(self.size, "bold"))

    def _drag_start(self, event) -> None:
        self._offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag(self, event) -> None:
        self.geometry(f"+{event.x_root - self._offset[0]}+{event.y_root - self._offset[1]}")

    def _save_position(self, _event=None) -> None:
        self.prefs.update(x=self.winfo_x(), y=self.winfo_y())

    def _resize_text(self, event) -> None:
        self.size = max(10, min(40, self.size + (2 if event.delta > 0 else -2)))
        self.prefs["size"] = self.size
        self._apply_size()

    def show(self, heard: str, said: str) -> None:
        self.heard.configure(text=heard)
        self.said.configure(text=said)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task | None = None
        self.level = 0.0
        self.shown_level = 0.0
        self.state = "idle"
        self.last_signal = 0.0
        self.mic_warned = False
        self.lines: dict[int, tuple] = {}  # altyazı satırları (dil değişince yeniden yazılır)
        self.prefs = settings.load_settings()
        i18n.set_language(self.prefs.get("ui_language") or i18n.system_language())
        self.mic_choice = self.prefs.get("input_device")  # bilgisayar sesine geçince unutulmasın
        self.run_source = "mic"
        self.overlay: Overlay | None = None
        self.last_line: tuple[str, str] | None = None  # altyazı penceresi açılınca gösterilir
        self.hotkey = GlobalHotkey(HOTKEY, lambda: self.events.put(("hotkey", None)))
        self.hotkey_ok = self.hotkey.start()

        root.title("Dialectra")
        root.configure(bg=C["face"])
        root.geometry(f"{px(820)}x{px(680)}")
        root.minsize(px(640), px(560))

        self._build_ui()
        self._load_devices()
        self._show_placeholder()
        self._set_status("idle", t("status_ready"))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_id = root.after(50, self._poll)

    # ------------------------------------------------------------- arayüz --

    def _build_ui(self) -> None:
        pad = px(6)
        TitleBar(self.root, "Dialectra", t("subtitle")).pack(
            fill="x", padx=pad, pady=(pad, 0)
        )

        toolbar = tk.Frame(self.root, bg=C["face"])
        toolbar.pack(fill="x", padx=pad, pady=(px(4), 0))
        self.overlay_btn = button(toolbar, t("overlay_button"), self.toggle_overlay)
        self.overlay_btn.pack(side="left")
        self.key_btn = button(toolbar, t("key_button"), self.ask_api_key)
        self.key_btn.pack(side="left", padx=(px(4), 0))

        self.lang_var = tk.StringVar(value=i18n.LANGUAGES[i18n.language()])
        self.lang_box = tk.OptionMenu(toolbar, self.lang_var, *i18n.LANGUAGES.values(),
                                      command=self._change_language)
        self.lang_box.configure(bg=C["face"], activebackground=C["face"], relief="raised", bd=2,
                                highlightthickness=0, font=font(9), width=8,
                                disabledforeground=C["shadow"])
        self.lang_box["menu"].configure(bg=C["window"], activebackground=C["navy"],
                                        activeforeground="white", font=font(9))
        self.lang_box.pack(side="right")
        tk.Label(toolbar, text=t("ui_language"), bg=C["face"]).pack(side="right", padx=(0, px(4)))

        # Alttakiler önce yerleşir ki pencere küçülünce altyazı alanı onları ezmesin.
        self._build_status_bar()
        controls = tk.Frame(self.root, bg=C["face"])
        controls.pack(side="bottom", fill="x", padx=pad, pady=(px(4), px(2)))
        self._build_controls(controls)

        # Altyazı alanı: beyaz, gömük
        box = tk.Frame(self.root, bg=C["face"], bd=2, relief="sunken")
        box.pack(fill="both", expand=True, padx=pad, pady=(px(6), 0))
        scroll = ttk.Scrollbar(box, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.feed = tk.Text(
            box, bg=C["window"], fg=C["text"], bd=0, wrap="word", padx=px(10), pady=px(8),
            font=font(12), cursor="arrow", yscrollcommand=scroll.set, state="disabled",
            highlightthickness=0, spacing3=px(1),
        )
        self.feed.pack(side="left", fill="both", expand=True)
        scroll.configure(command=self.feed.yview)
        self.feed.tag_configure("pill_en", background=C["en"], foreground="white",
                                font=font(8, "bold"))
        self.feed.tag_configure("pill_tr", background=C["tr"], foreground="white",
                                font=font(8, "bold"))
        self.feed.tag_configure("live", foreground=C["shadow"], font=font(8))
        self.feed.tag_configure("heard", foreground=C["muted"], font=font(9), spacing1=px(3))
        self.feed.tag_configure("said", font=font(12), spacing1=px(1))
        self.feed.tag_configure("placeholder_title", justify="center", font=font(11, "bold"),
                                spacing1=px(120))
        self.feed.tag_configure("placeholder", justify="center", foreground=C["muted"],
                                font=font(9), spacing1=px(4))

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.root, bg=C["face"])
        bar.pack(side="bottom", fill="x", padx=px(2), pady=px(2))

        def panel(**kw) -> tk.Frame:
            frame = tk.Frame(bar, bg=C["face"], bd=1, relief="sunken")
            frame.pack(**kw)
            return frame

        chip = panel(side="left")
        self.dot = tk.Label(chip, text="●", bg=C["face"], fg=C["idle"], font=font(9))
        self.dot.pack(side="left", padx=(px(4), 0))
        self.chip_label = tk.Label(chip, text="", bg=C["face"], width=10, anchor="w")
        self.chip_label.pack(side="left", padx=(px(2), px(4)))

        meter_box = panel(side="right", padx=(px(2), 0))
        tk.Label(meter_box, text=t("volume"), bg=C["face"]).pack(side="left", padx=(px(4), px(4)))
        self.meter = tk.Canvas(meter_box, width=px(METER_BLOCKS * 9 + 4), height=px(14),
                               bg=C["window"], bd=1, relief="sunken", highlightthickness=0)
        self.meter.pack(side="left", padx=(0, px(4)), pady=px(2))

        message = panel(side="left", fill="x", expand=True, padx=(px(2), 0))
        self.status_label = tk.Label(message, text="", bg=C["face"], anchor="w")
        self.status_label.pack(fill="x", padx=px(4))

    def _build_controls(self, box: tk.Frame) -> None:
        box.columnconfigure((0, 1), weight=1, uniform="col")

        source = group(box, t("group_source"))
        source.grid(row=0, column=0, sticky="nsew", padx=(0, px(3)))
        self.source_var = tk.StringVar(value=self.prefs.get("source", "mic"))
        self.source_btns = [
            choice(source, tk.Radiobutton, text=t(f"source_{key}"), value=key,
                   variable=self.source_var, command=self._apply_source)
            for key in SOURCES
        ]
        for btn in self.source_btns:
            btn.pack(side="left", padx=(0, px(10)))

        direction = group(box, t("group_direction"))
        direction.grid(row=0, column=1, sticky="nsew", padx=(px(3), 0))
        self.dir_var = tk.StringVar(value=self.prefs.get("direction", "both"))
        self.dir_btns = [
            choice(direction, tk.Radiobutton, text=label or t(f"direction_{key}"), value=key,
                   variable=self.dir_var)
            for key, label in DIRECTIONS.items()
        ]
        for btn in self.dir_btns:
            btn.pack(side="left", padx=(0, px(10)))

        devices = group(box, t("group_devices"))
        devices.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(px(4), 0))
        devices.columnconfigure((1, 3), weight=1, uniform="dev")

        def picker(col: int, title: str, command=None):
            label = tk.Label(devices, text=title, bg=C["face"], anchor="w")
            label.grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else px(10), px(4)))
            var = tk.StringVar()
            menu = tk.OptionMenu(devices, var, "")
            menu.configure(bg=C["face"], activebackground=C["face"], relief="raised", bd=2,
                           highlightthickness=0, anchor="w", width=1, font=font(9),
                           disabledforeground=C["shadow"])
            menu["menu"].configure(bg=C["window"], activebackground=C["navy"],
                                   activeforeground="white", font=font(9))
            menu.grid(row=0, column=col + 1, sticky="ew", pady=px(2))
            return var, menu, label, command

        self.mic_var, self.mic_box, self.mic_label, self.mic_command = picker(
            0, t("mic"), command=lambda v: setattr(self, "mic_choice", v)
        )
        self.out_var, self.out_box, _, _ = picker(2, t("speaker"))

        row = tk.Frame(box, bg=C["face"])
        row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(px(6), px(2)))
        self.hp_var = tk.BooleanVar(value=self.prefs.get("headphones", False))
        self.hp_check = choice(row, tk.Checkbutton, text=t("headphones"),
                               variable=self.hp_var)
        self.hp_check.pack(side="left")
        button(
            row, "?", lambda: self._set_status(None, t("headphones_tip")), padx=px(5), pady=0,
        ).pack(side="left", padx=(px(2), px(14)))
        self.speak_var = tk.BooleanVar(value=self.prefs.get("speak", True))
        self.speak_check = choice(row, tk.Checkbutton, text=t("speak"),
                                  variable=self.speak_var)
        self.speak_check.pack(side="left")

        self.start_btn = button(row, t("start"), self.toggle, width=10, default="active",
                                font=font(9, "bold"))
        self.start_btn.pack(side="right")
        self.clear_btn = button(row, t("clear"), self.clear, width=10)
        self.clear_btn.pack(side="right", padx=(0, px(6)))

    @staticmethod
    def _set_options(menu: tk.OptionMenu, var: tk.StringVar, values: list[str], command=None):
        items = menu["menu"]
        items.delete(0, "end")
        for value in values:
            def pick(v=value):
                var.set(v)
                if command:
                    command(v)
            items.add_command(label=value, command=pick)

    def _load_devices(self) -> None:
        try:
            self.inputs, self.outputs = list_devices()
        except Exception:  # noqa: BLE001
            self.inputs, self.outputs = [], []
        out_values = [t("default_device")] + [n for _, n in self.outputs]
        self._set_options(self.out_box, self.out_var, out_values)
        saved_out = self.prefs.get("output_device")
        self.out_var.set(saved_out if saved_out in out_values else t("default_device"))
        self._apply_source()

    def source(self) -> str:
        return self.source_var.get()

    def _apply_source(self) -> None:
        """Giriş cihazı menüsünü seçili kaynağa (mikrofon / bilgisayar sesi) göre ayarlar."""
        system = self.source() == "system"
        state = "disabled" if system else "normal"
        if system:
            # Dialectra dışındaki bütün ses yakalanır; seçilecek cihaz yok.
            self.mic_label.configure(text=t("listening_to"))
            self._set_options(self.mic_box, self.mic_var, [t("all_audio")])
            self.mic_var.set(t("all_audio"))
        else:
            values = [t("default_device")] + [n for _, n in self.inputs]
            self.mic_label.configure(text=t("mic"))
            self._set_options(self.mic_box, self.mic_var, values, self.mic_command)
            self.mic_var.set(self.mic_choice if self.mic_choice in values else t("default_device"))
        # Bilgisayar sesinde Dialectra'nın kendi sesi duyulmaz; kulaklık seçeneği anlamsız.
        self.mic_box.configure(state=state)
        self.hp_check.configure(state=state)

    @staticmethod
    def _device_id(name: str, devices: list[tuple[int, str]]) -> int | None:
        return next((i for i, n in devices if n == name), None)

    # ------------------------------------------------------------ altyazı --

    def _show_placeholder(self) -> None:
        self.feed.configure(state="normal")
        self.feed.delete("1.0", "end")
        self.lines = {}
        hint = t("placeholder")
        if self.hotkey_ok:
            hint += "\n\n" + t("placeholder_hotkey", hotkey=HOTKEY_LABEL)
        self.feed.insert("end", t("placeholder_title") + "\n", "placeholder_title",
                         hint, "placeholder")
        self.feed.configure(state="disabled")

    def update_translation(
        self, line_id: int, source: str, heard: str, target: str, said: str, final: bool
    ) -> None:
        """Satırı ekler; aynı satır_no tekrar gelirse yerinde günceller (metin akarken).

        Her satırın metni kendi etiketini (`line<no>`) taşır; güncellerken o aralık
        silinip yerine yenisi yazılır.
        """
        at_bottom = self.feed.yview()[1] >= 0.999
        tag = f"line{line_id}"
        self.feed.configure(state="normal")
        if not self.lines:
            self.feed.delete("1.0", "end")  # yer tutucu yazı
        ranges = self.feed.tag_ranges(tag)
        if ranges:
            index = ranges[0]
            self.feed.delete(ranges[0], ranges[-1])
        else:
            if self.lines:
                self.feed.insert("end-1c", "\n")  # satırlar arası boşluk (etiketsiz)
            index = self.feed.index("end-1c")
        self.lines[line_id] = (source, heard, target, said, final)

        parts = [f" {source.upper()} → {target.upper()} ", (f"pill_{target}", tag)]
        if not final:
            parts += [t("translating"), ("live", tag)]
        parts += ["\n", (tag,)]
        if heard:
            parts += [heard + "\n", ("heard", tag)]
        parts += [said + "\n", ("said", tag)]
        self.feed.insert(index, *parts)
        self.feed.configure(state="disabled")

        self.last_line = (heard, said)
        if self.overlay is not None:
            self.overlay.show(heard, said)
        if at_bottom:  # kullanıcı yukarı kaydırıp eski satırları okuyorsa yerinden oynatma
            self.feed.see("end")

    def clear(self) -> None:
        self._show_placeholder()

    def toggle_overlay(self) -> None:
        if self.overlay is not None:
            self._close_overlay()
            return
        self.overlay = Overlay(self.root, self.prefs.setdefault("overlay", {}),
                               on_close=self._close_overlay)
        if self.last_line:
            self.overlay.show(*self.last_line)
        self.overlay_btn.configure(relief="sunken")

    def _close_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.destroy()
            self.overlay = None
            settings.save_settings(self.prefs)  # konum ve yazı boyutu
        self.overlay_btn.configure(relief="raised")

    def _change_language(self, name: str) -> None:
        """Arayüzü seçilen dilde yeniden kurar; seçimler ve altyazılar korunur."""
        language = next(k for k, v in i18n.LANGUAGES.items() if v == name)
        if language == i18n.language():
            return
        i18n.set_language(language)
        self.prefs.update(ui_language=language, source=self.source(), direction=self.dir_var.get(),
                          headphones=self.hp_var.get(), speak=self.speak_var.get(),
                          input_device=self.mic_choice, output_device=self.out_var.get())
        settings.save_settings(self.prefs)
        lines = self.lines
        reopen = self.overlay is not None
        self._close_overlay()
        for child in self.root.winfo_children():
            child.destroy()
        self._build_ui()
        self._load_devices()
        self._show_placeholder()
        for line_id, data in lines.items():
            self.update_translation(line_id, *data)
        self._set_status("idle", t("status_ready"))
        if reopen:
            self.toggle_overlay()

    # ------------------------------------------------------------- durum --

    def _set_status(self, state: str | None, message: str) -> None:
        if state:
            self.dot.configure(fg=STATUS_COLORS[state])
            self.chip_label.configure(text=t(f"state_{state}"))
        self.status_label.configure(text=message,
                                    fg=C["error"] if state == "error" else C["text"])

    def _draw_meter(self, level: float) -> None:
        """Windows 95 ilerleme çubuğu gibi lacivert bloklar."""
        self.meter.delete("all")
        lit = round(level * METER_BLOCKS)
        size, gap = px(7), px(2)
        h = int(self.meter["height"])
        for i in range(lit):
            x = px(3) + i * (size + gap)
            self.meter.create_rectangle(x, px(2), x + size, h - px(1), outline="",
                                        fill=C["navy"])

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in (*self.source_btns, *self.dir_btns, self.mic_box, self.out_box,
                       self.hp_check, self.speak_check, self.key_btn, self.lang_box):
            widget.configure(state=state)
        if not running:
            self._apply_source()
        self.start_btn.configure(text=t("stop") if running else t("start"))

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "text":
                    self.update_translation(*data)
                elif kind == "status":
                    self.state = data[0]
                    self.mic_warned = False
                    self._set_status(*data)
                elif kind == "error":
                    self._set_running(False)
                    self._set_status("error", data)
                elif kind == "stopped":
                    self._set_running(False)
                    self._set_status("idle", t("status_stopped"))
                elif kind == "hotkey":
                    self.toggle()
        except queue.Empty:
            pass

        # Ses göstergesi: hızlı yüksel, yavaş düş.
        running = self.thread is not None and self.thread.is_alive()
        target = self.level if running else 0.0
        shown = target if target > self.shown_level else self.shown_level * 0.85
        if round(shown * METER_BLOCKS) != round(self.shown_level * METER_BLOCKS):
            self._draw_meter(shown)
        self.shown_level = shown

        # Mikrofon tamamen sessizse (ortam gürültüsü bile yoksa) muhtemelen
        # Windows'ta sessize alınmış ya da seviyesi sıfırdır. Bilgisayar sesinde
        # sessizlik olağandır (hiçbir şey çalmıyor olabilir).
        listening = running and self.run_source == "mic"
        if listening and self.level > SIGNAL_LEVEL:
            self.last_signal = time.monotonic()
            if self.mic_warned:
                self.mic_warned = False
                self._set_status("live", t("status_listening_again"))
        elif (
            listening
            and self.state == "live"
            and not self.mic_warned
            and time.monotonic() - self.last_signal > MIC_SILENT_AFTER
        ):
            self.mic_warned = True
            self._set_status("reconnecting", t("status_mic_silent"))

        self.poll_id = self.root.after(50, self._poll)

    # ------------------------------------------------------------ çeviri --

    def toggle(self) -> None:
        if self.thread and self.thread.is_alive():
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        api_key = settings.load_api_key()
        if not api_key:
            self.ask_api_key(then=self.start)
            return

        source = self.source()
        self.prefs.update(
            input_device=self.mic_choice,
            output_device=self.out_var.get(),
            headphones=self.hp_var.get(),
            source=source,
            direction=self.dir_var.get(),
            speak=self.speak_var.get(),
        )
        settings.save_settings(self.prefs)
        self.run_source = source
        translator = LiveTranslator(
            api_key=api_key,
            input_device=self._device_id(self.mic_choice, self.inputs),
            output_device=self._device_id(self.out_var.get(), self.outputs),
            headphones=self.hp_var.get(),
            source=source,
            direction=self.dir_var.get(),
            speak=self.speak_var.get(),
            on_text=lambda *a: self.events.put(("text", a)),
            on_status=lambda s, m: self.events.put(("status", (s, m))),
            on_level=self._on_level,
        )
        self.last_signal = time.monotonic()
        self.mic_warned = False
        self.thread = threading.Thread(target=self._run_translator, args=(translator,), daemon=True)
        self.thread.start()
        self._set_running(True)
        self._set_status("connecting", t("status_connecting_gemini"))

    def _on_level(self, level: float) -> None:
        self.level = level  # ses iş parçacığından; tek bir float ataması güvenli

    def _run_translator(self, translator: LiveTranslator) -> None:
        async def main() -> None:
            self.task = asyncio.current_task()
            await translator.run()

        self.loop = asyncio.new_event_loop()
        try:
            self.loop.run_until_complete(main())
            self.events.put(("stopped", None))
        except asyncio.CancelledError:
            self.events.put(("stopped", None))
        except FatalError as exc:
            self.events.put(("error", str(exc)))
        except sd.PortAudioError as exc:
            self.events.put(("error", t("error_device", error=exc)))
        except AudioDeviceError as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", t("error_unexpected", error=f"{type(exc).__name__}: {exc}")))
        finally:
            self.loop.close()
            self.loop, self.task, self.level = None, None, 0.0

    def stop(self) -> None:
        loop, task = self.loop, self.task
        if loop and task:
            loop.call_soon_threadsafe(task.cancel)
        self._set_status("idle", t("status_stopping"))

    def on_close(self) -> None:
        if self.thread and self.thread.is_alive():
            self.stop()
            self.thread.join(timeout=2)
        self.hotkey.stop()
        self._close_overlay()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

    # ------------------------------------------------------- API anahtarı --

    def ask_api_key(self, then=None) -> None:
        win = tk.Toplevel(self.root, bg=C["face"])
        win.title(t("key_title"))
        win.resizable(False, False)
        win.transient(self.root)

        body = tk.Frame(win, bg=C["face"])
        body.pack(padx=px(14), pady=px(12))
        tk.Label(
            body, bg=C["face"], justify="left", anchor="w", wraplength=px(380),
            text=t("key_info"),
        ).pack(fill="x")
        link = tk.Label(body, text=t("key_link"),
                        bg=C["face"], fg="#0000FF", cursor="hand2", font=font(9, "underline"))
        link.pack(anchor="w", pady=(px(6), px(10)))
        link.bind("<Button-1>", lambda e: webbrowser.open(API_KEY_URL))

        tk.Label(body, text=t("key_label"), bg=C["face"]).pack(anchor="w")
        key_var = tk.StringVar(value=settings.load_api_key())
        entry = tk.Entry(body, textvariable=key_var, show="•", width=48, bg=C["window"],
                         relief="sunken", bd=2, font=font(10))
        entry.pack(fill="x", pady=(px(2), 0), ipady=px(2))
        win.after(150, entry.focus_set)

        show_var = tk.BooleanVar(value=False)
        choice(body, tk.Checkbutton, text=t("key_show"), variable=show_var,
               command=lambda: entry.configure(show="" if show_var.get() else "•")).pack(
            anchor="w", pady=(px(6), 0)
        )

        def save(_event=None) -> None:
            key = key_var.get().strip()
            if not key:
                entry.focus_set()
                return
            settings.save_api_key(key)
            win.destroy()
            if then:
                then()

        buttons = tk.Frame(body, bg=C["face"])
        buttons.pack(fill="x", pady=(px(12), 0))
        button(buttons, t("cancel"), win.destroy, width=10).pack(side="right")
        button(buttons, t("ok"), save, width=10, default="active").pack(
            side="right", padx=(0, px(6))
        )
        win.bind("<Return>", save)
        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.after(100, win.grab_set)


def main() -> None:
    root = create_root()
    App(root)
    root.mainloop()
