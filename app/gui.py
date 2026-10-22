"""Dialectra masaüstü arayüzü (CustomTkinter).

Çeviri motoru (asyncio) ayrı bir iş parçacığında çalışır; arayüzle bir kuyruk
üzerinden haberleşir. tkinter'a yalnızca ana iş parçacığından dokunulur.
"""

import asyncio
import queue
import threading
import time
import webbrowser

import customtkinter as ctk
import sounddevice as sd

from app import settings
from app.live_translate import FatalError, LiveTranslator, list_devices

C = {
    "bg": "#0B0D12",
    "card": "#14171F",
    "card_alt": "#1A1E28",
    "border": "#262B37",
    "hover": "#222734",
    "text": "#ECEEF3",
    "muted": "#8B93A3",
    "faint": "#5A6172",
    "accent": "#7C5CFF",
    "accent_hover": "#8F73FF",
    "stop": "#F0454F",
    "stop_hover": "#FF5A63",
    "en": "#4FA3FF",
    "tr": "#FF6B81",
    "live": "#34D399",
    "warn": "#FBBF24",
    "error": "#F87171",
    "idle": "#5A6172",
}
STATUS = {  # durum -> (renk, kısa etiket)
    "idle": (C["idle"], "Hazır"),
    "connecting": (C["warn"], "Bağlanıyor"),
    "reconnecting": (C["warn"], "Uyarı"),
    "live": (C["live"], "Canlı"),
    "error": (C["error"], "Hata"),
}
LANG_NAMES = {"en": "İngilizce", "tr": "Türkçe"}
FONT = "Segoe UI"
SIGNAL_LEVEL = 0.0005  # bunun altı "hiç sinyal yok" (ortam gürültüsü bile değil)
MIC_SILENT_AFTER = 4.0  # sn
DEFAULT_DEVICE = "Sistem varsayılanı"
API_KEY_URL = "https://aistudio.google.com/apikey"


def font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


class LineCard(ctk.CTkFrame):
    """Tek bir çeviri satırı: duyulan cümle (küçük) ve çevirisi (büyük)."""

    def __init__(self, master, wrap: int) -> None:
        super().__init__(master, fg_color=C["card_alt"], corner_radius=14, border_width=1,
                         border_color=C["border"])
        self.columnconfigure(1, weight=1)
        self.bar = ctk.CTkFrame(self, width=4, height=8, corner_radius=2, fg_color=C["accent"])
        self.bar.grid(row=0, column=0, rowspan=3, sticky="ns", padx=(10, 0), pady=12)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=1, sticky="ew", padx=14, pady=(10, 0))
        self.pill = ctk.CTkLabel(head, text="", height=20, corner_radius=10, font=font(10, "bold"),
                                 text_color=C["bg"], fg_color=C["accent"])
        self.pill.pack(side="left")
        self.live_tag = ctk.CTkLabel(head, text="● çevriliyor", font=font(10),
                                     text_color=C["faint"])

        self.heard = ctk.CTkLabel(self, text="", font=font(12), text_color=C["muted"],
                                  justify="left", anchor="w", wraplength=wrap)
        self.heard.grid(row=1, column=1, sticky="ew", padx=14, pady=(6, 0))
        self.said = ctk.CTkLabel(self, text="", font=font(17), text_color=C["text"],
                                 justify="left", anchor="w", wraplength=wrap)
        self.said.grid(row=2, column=1, sticky="ew", padx=14, pady=(2, 12))

    def update_line(self, source: str, heard: str, target: str, said: str, final: bool) -> None:
        color = C[target]
        self.bar.configure(fg_color=color)
        self.pill.configure(text=f"  {source.upper()}  →  {target.upper()}  ", fg_color=color)
        if heard:
            self.heard.configure(text=heard)
            self.heard.grid()
        else:
            self.heard.grid_remove()
        self.said.configure(text=said)
        if final:
            self.live_tag.pack_forget()
            self.configure(border_color=C["border"])
        else:
            self.live_tag.pack(side="left", padx=(10, 0))
            self.configure(border_color=color)

    def set_wrap(self, wrap: int) -> None:
        self.heard.configure(wraplength=wrap)
        self.said.configure(wraplength=wrap)


class App:
    def __init__(self, root: ctk.CTk) -> None:
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
        self.cards: dict[int, LineCard] = {}
        self.wrap = 560
        self.prefs = settings.load_settings()

        root.title("Dialectra")
        root.configure(fg_color=C["bg"])
        root.geometry("860x700")
        root.minsize(640, 520)

        self._build_ui()
        self._load_devices()
        self._show_placeholder()
        self._set_status("idle", "Başlamak için 'Başlat'a bas.")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_id = root.after(50, self._poll)

    # ------------------------------------------------------------- arayüz --

    def _button(self, parent, text, command, kind="accent", **kw) -> ctk.CTkButton:
        if kind == "ghost":
            colors = dict(fg_color=C["card_alt"], hover_color=C["hover"], text_color=C["text"],
                          border_width=1, border_color=C["border"])
        else:
            colors = dict(fg_color=C[kind], hover_color=C[f"{kind}_hover"], text_color="white")
        opts = dict(height=40, corner_radius=12, font=font(13, "bold"), **colors)
        opts.update(kw)
        return ctk.CTkButton(parent, text=text, command=command, **opts)

    def _build_ui(self) -> None:
        pad = 22

        # Başlık: logo, ad, alt başlık; sağda API anahtarı düğmesi
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=pad, pady=(pad, 0))
        logo = ctk.CTkLabel(header, text="⇄", width=44, height=44, corner_radius=12,
                            fg_color=C["accent"], text_color="white", font=font(22, "bold"))
        logo.pack(side="left")
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(titles, text="Dialectra", font=font(22, "bold"), text_color=C["text"],
                     height=26).pack(anchor="w")
        ctk.CTkLabel(titles, text="İngilizce ↔ Türkçe canlı sesli çeviri", font=font(12),
                     text_color=C["muted"], height=18).pack(anchor="w")
        self.key_btn = self._button(header, "API anahtarı", self.ask_api_key, kind="ghost",
                                    height=36, font=font(12), width=140)
        self.key_btn.pack(side="right")

        # Durum satırı: renkli çip + açıklama
        status = ctk.CTkFrame(self.root, fg_color="transparent")
        status.pack(fill="x", padx=pad, pady=(16, 10))
        self.chip = ctk.CTkFrame(status, fg_color=C["card"], corner_radius=14, border_width=1,
                                 border_color=C["border"])
        self.chip.pack(side="left")
        self.dot = ctk.CTkLabel(self.chip, text="●", font=font(12), text_color=C["idle"], width=10)
        self.dot.pack(side="left", padx=(12, 4), pady=2)
        self.chip_label = ctk.CTkLabel(self.chip, text="", font=font(12, "bold"),
                                       text_color=C["text"], height=24)
        self.chip_label.pack(side="left", padx=(0, 12))
        self.status_label = ctk.CTkLabel(status, text="", font=font(12), text_color=C["muted"],
                                         anchor="w", justify="left")
        self.status_label.pack(side="left", padx=(12, 0), fill="x", expand=True)

        # Kontroller önce alta yerleşir ki pencere küçülünce altyazı alanı onları ezmesin.
        controls = ctk.CTkFrame(self.root, fg_color=C["card"], corner_radius=18, border_width=1,
                                border_color=C["border"])
        controls.pack(side="bottom", fill="x", padx=pad, pady=(12, pad))
        self._build_controls(controls)

        # Altyazı alanı
        self.feed = ctk.CTkScrollableFrame(
            self.root, fg_color=C["card"], corner_radius=18, border_width=1,
            border_color=C["border"], scrollbar_button_color=C["border"],
            scrollbar_button_hover_color=C["faint"],
        )
        self.feed.pack(fill="both", expand=True, padx=pad)
        self.feed.columnconfigure(0, weight=1)
        # add="+": CTkScrollableFrame kaydırma alanını kendi <Configure> bağlantısıyla günceller.
        self.feed._parent_canvas.bind("<Configure>", self._on_feed_resize, add="+")

    def _build_controls(self, box: ctk.CTkFrame) -> None:
        box.columnconfigure((0, 1), weight=1, uniform="dev")

        def device_picker(col: int, title: str) -> tuple[ctk.StringVar, ctk.CTkOptionMenu]:
            cell = ctk.CTkFrame(box, fg_color="transparent")
            cell.grid(row=0, column=col, sticky="ew", padx=(18 if col == 0 else 8, 18 if col else 8),
                      pady=(16, 0))
            ctk.CTkLabel(cell, text=title, font=font(11, "bold"),
                         text_color=C["muted"], height=18).pack(anchor="w")
            var = ctk.StringVar()
            menu = ctk.CTkOptionMenu(
                cell, variable=var, height=36, corner_radius=10, font=font(12),
                fg_color=C["card_alt"], button_color=C["card_alt"], button_hover_color=C["hover"],
                text_color=C["text"], dropdown_fg_color=C["card_alt"],
                dropdown_hover_color=C["accent"], dropdown_text_color=C["text"],
                dropdown_font=font(12), dynamic_resizing=False,
            )
            menu.pack(fill="x", pady=(6, 0))
            return var, menu

        self.mic_var, self.mic_box = device_picker(0, "Mikrofon")
        self.out_var, self.out_box = device_picker(1, "Hoparlör")

        row = ctk.CTkFrame(box, fg_color="transparent")
        row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=16)

        self.hp_var = ctk.BooleanVar(value=self.prefs.get("headphones", False))
        self.hp_check = ctk.CTkSwitch(
            row, text="Kulaklık kullanıyorum", variable=self.hp_var, font=font(12),
            text_color=C["text"], progress_color=C["accent"], fg_color=C["border"],
            button_color=C["text"], button_hover_color="white",
        )
        self.hp_check.pack(side="left")
        tip = ctk.CTkLabel(row, text="ⓘ", font=font(13), text_color=C["muted"], cursor="hand2",
                           width=18)
        tip.pack(side="left", padx=(4, 20))
        tip.bind(
            "<Button-1>",
            lambda e: self._set_status(
                None,
                "Kulaklık yoksa, hoparlörden çıkan çevirinin mikrofona geri dönüp "
                "tekrar çevrilmesi engellenir. Kulaklıkla buna gerek yok.",
            ),
        )

        ctk.CTkLabel(row, text="Ses", font=font(11, "bold"), text_color=C["muted"]).pack(side="left")
        self.meter = ctk.CTkProgressBar(row, width=130, height=8, corner_radius=4,
                                        fg_color=C["card_alt"], progress_color=C["live"])
        self.meter.set(0)
        self.meter.pack(side="left", padx=(10, 0))

        self.start_btn = self._button(row, "●  Başlat", self.toggle, width=130, height=44,
                                      corner_radius=22, font=font(14, "bold"))
        self.start_btn.pack(side="right")
        self.clear_btn = self._button(row, "Temizle", self.clear, kind="ghost", width=100,
                                      height=44, corner_radius=22)
        self.clear_btn.pack(side="right", padx=(0, 10))

    def _load_devices(self) -> None:
        try:
            self.inputs, self.outputs = list_devices()
        except Exception:  # noqa: BLE001
            self.inputs, self.outputs = [], []
        mic_values = [DEFAULT_DEVICE] + [n for _, n in self.inputs]
        out_values = [DEFAULT_DEVICE] + [n for _, n in self.outputs]
        self.mic_box.configure(values=mic_values)
        self.out_box.configure(values=out_values)
        saved_in, saved_out = self.prefs.get("input_device"), self.prefs.get("output_device")
        self.mic_var.set(saved_in if saved_in in mic_values else DEFAULT_DEVICE)
        self.out_var.set(saved_out if saved_out in out_values else DEFAULT_DEVICE)

    @staticmethod
    def _device_id(name: str, devices: list[tuple[int, str]]) -> int | None:
        return next((i for i, n in devices if n == name), None)

    # ------------------------------------------------------------ altyazı --

    def _show_placeholder(self) -> None:
        for child in self.feed.winfo_children():
            child.destroy()
        self.cards = {}
        self.placeholder = ctk.CTkFrame(self.feed, fg_color="transparent")
        self.placeholder.grid(row=0, column=0, pady=(150, 0))
        ctk.CTkLabel(self.placeholder, text="Çeviriler burada görünecek", font=font(16, "bold"),
                     text_color=C["text"]).pack(pady=(0, 4))
        ctk.CTkLabel(
            self.placeholder,
            text="İngilizce konuşursan Türkçe, Türkçe konuşursan İngilizce duyarsın.\n"
                 "Dil seçmene gerek yok.",
            font=font(12), text_color=C["muted"], justify="center",
        ).pack()

    def _on_feed_resize(self, event) -> None:
        wrap = max(200, event.width - 80)
        if abs(wrap - self.wrap) > 4:
            self.wrap = wrap
            for card in self.cards.values():
                card.set_wrap(wrap)

    def update_translation(
        self, line_id: int, source: str, heard: str, target: str, said: str, final: bool
    ) -> None:
        """Satırı ekler; aynı satır_no tekrar gelirse yerinde günceller (metin akarken)."""
        canvas = self.feed._parent_canvas
        at_bottom = canvas.yview()[1] >= 0.999
        if not self.cards and self.placeholder is not None:
            self.placeholder.destroy()
            self.placeholder = None
        card = self.cards.get(line_id)
        if card is None:
            card = LineCard(self.feed, self.wrap)
            card.grid(row=len(self.cards), column=0, sticky="ew", padx=10, pady=(10, 0))
            self.cards[line_id] = card
        card.update_line(source, heard, target, said, final)

        if at_bottom:  # kullanıcı yukarı kaydırıp eski satırları okuyorsa yerinden oynatma
            # Kaydırma alanı yerleşimden sonra güncellenir; o yüzden biraz sonra kaydır.
            self.root.after(30, lambda: canvas.yview_moveto(1.0))

    def clear(self) -> None:
        self._show_placeholder()

    # ------------------------------------------------------------- durum --

    def _set_status(self, state: str | None, message: str) -> None:
        if state:
            color, label = STATUS[state]
            self.dot.configure(text_color=color)
            self.chip_label.configure(text=label)
            self.chip.configure(border_color=color if state != "idle" else C["border"])
        self.status_label.configure(text=message,
                                    text_color=C["error"] if state == "error" else C["muted"])

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in (self.mic_box, self.out_box, self.hp_check, self.key_btn):
            widget.configure(state=state)
        kind = "stop" if running else "accent"
        self.start_btn.configure(text="■  Durdur" if running else "●  Başlat",
                                 fg_color=C[kind], hover_color=C[f"{kind}_hover"])

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
                    self._set_status("idle", "Durduruldu.")
        except queue.Empty:
            pass

        # Ses göstergesi: hızlı yüksel, yavaş düş.
        running = self.thread is not None and self.thread.is_alive()
        target = self.level if running else 0.0
        shown = target if target > self.shown_level else self.shown_level * 0.85
        if abs(shown - self.shown_level) > 0.002 or (shown == 0 and self.shown_level):
            self.meter.set(shown)
        self.shown_level = shown

        # Mikrofon tamamen sessizse (ortam gürültüsü bile yoksa) muhtemelen
        # Windows'ta sessize alınmış ya da seviyesi sıfırdır.
        if running and self.level > SIGNAL_LEVEL:
            self.last_signal = time.monotonic()
            if self.mic_warned:
                self.mic_warned = False
                self._set_status("live", "Dinliyor — konuşmaya başlayabilirsin.")
        elif (
            running
            and self.state == "live"
            and not self.mic_warned
            and time.monotonic() - self.last_signal > MIC_SILENT_AFTER
        ):
            self.mic_warned = True
            self._set_status(
                "reconnecting",
                "Mikrofondan ses gelmiyor. Windows'ta sessize alınmış ya da seviyesi sıfır olabilir.",
            )

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

        settings.save_settings(
            {
                "input_device": self.mic_var.get(),
                "output_device": self.out_var.get(),
                "headphones": self.hp_var.get(),
            }
        )
        translator = LiveTranslator(
            api_key=api_key,
            input_device=self._device_id(self.mic_var.get(), self.inputs),
            output_device=self._device_id(self.out_var.get(), self.outputs),
            headphones=self.hp_var.get(),
            on_text=lambda *a: self.events.put(("text", a)),
            on_status=lambda s, m: self.events.put(("status", (s, m))),
            on_level=self._on_level,
        )
        self.last_signal = time.monotonic()
        self.mic_warned = False
        self.thread = threading.Thread(target=self._run_translator, args=(translator,), daemon=True)
        self.thread.start()
        self._set_running(True)
        self._set_status("connecting", "Gemini'ye bağlanıyor...")

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
            self.events.put(("error", f"Ses cihazı açılamadı: {exc}"))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", f"Beklenmeyen hata: {type(exc).__name__}: {exc}"))
        finally:
            self.loop.close()
            self.loop, self.task, self.level = None, None, 0.0

    def stop(self) -> None:
        loop, task = self.loop, self.task
        if loop and task:
            loop.call_soon_threadsafe(task.cancel)
        self._set_status("idle", "Durduruluyor...")

    def on_close(self) -> None:
        if self.thread and self.thread.is_alive():
            self.stop()
            self.thread.join(timeout=2)
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

    # ------------------------------------------------------- API anahtarı --

    def ask_api_key(self, then=None) -> None:
        win = ctk.CTkToplevel(self.root)
        win.title("Gemini API anahtarı")
        win.configure(fg_color=C["bg"])
        win.resizable(False, False)
        win.transient(self.root)

        body = ctk.CTkFrame(win, fg_color=C["card"], corner_radius=18, border_width=1,
                            border_color=C["border"])
        body.pack(padx=16, pady=16)
        inner = ctk.CTkFrame(body, fg_color="transparent")
        inner.pack(padx=24, pady=22)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="Gemini API anahtarı", font=font(17, "bold"),
                     text_color=C["text"]).pack(side="left")

        ctk.CTkLabel(
            inner,
            text="Anahtar yalnızca bu bilgisayarda, uygulama klasöründeki .env dosyasında saklanır.",
            font=font(12), text_color=C["muted"], wraplength=400, justify="left",
        ).pack(anchor="w", pady=(14, 2))
        link = ctk.CTkLabel(inner, text="Ücretsiz anahtar al → aistudio.google.com/apikey",
                            font=font(12, "bold"), text_color=C["accent_hover"], cursor="hand2")
        link.pack(anchor="w", pady=(0, 14))
        link.bind("<Button-1>", lambda e: webbrowser.open(API_KEY_URL))

        key_var = ctk.StringVar(value=settings.load_api_key())
        entry = ctk.CTkEntry(
            inner, textvariable=key_var, show="•", width=420, height=42, corner_radius=10,
            fg_color=C["card_alt"], border_color=C["border"], text_color=C["text"], font=font(13),
            placeholder_text="AIza...",
        )
        entry.pack(fill="x")
        win.after(150, entry.focus_set)

        show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            inner, text="Anahtarı göster", variable=show_var, font=font(12),
            text_color=C["muted"], fg_color=C["accent"], hover_color=C["accent_hover"],
            border_color=C["border"], checkbox_width=18, checkbox_height=18, corner_radius=5,
            command=lambda: entry.configure(show="" if show_var.get() else "•"),
        ).pack(anchor="w", pady=(10, 0))

        def save(_event=None) -> None:
            key = key_var.get().strip()
            if not key:
                entry.focus_set()
                return
            settings.save_api_key(key)
            win.destroy()
            if then:
                then()

        buttons = ctk.CTkFrame(inner, fg_color="transparent")
        buttons.pack(fill="x", pady=(20, 0))
        self._button(buttons, "Kaydet", save, width=110).pack(side="right")
        self._button(buttons, "İptal", win.destroy, kind="ghost", width=100).pack(
            side="right", padx=(0, 10)
        )
        win.bind("<Return>", save)
        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.after(100, win.grab_set)


def main() -> None:
    ctk.set_appearance_mode("dark")  # koyu başlık çubuğu ve DPI ölçeklemesini de ayarlar
    root = ctk.CTk()
    App(root)
    root.mainloop()
