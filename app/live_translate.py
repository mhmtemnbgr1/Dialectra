"""Canlı sesli çeviri: Gemini 3.5 Live Translate (İngilizce <-> Türkçe).

Mikrofon sesi aynı anda iki oturuma gönderilir: biri her şeyi Türkçeye,
diğeri İngilizceye çevirir. `echo_target_language=False` sayesinde zaten hedef
dilde olan konuşmada oturum susar; böylece hangi dilde konuşulursa konuşulsun
yalnızca doğru oturum ses üretir.

Tek yön modunda yalnızca bir oturum açılır (ücretsiz kotanın yarısı harcanır).
Ses kaynağı mikrofon ya da bilgisayarın çaldığı ses (`app.system_audio`) olabilir.

Her oturum bağımsızdır: biri koparsa yalnızca o yeniden bağlanır.
"""

import asyncio
import contextlib
import re
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import errors, types

from app.i18n import t
from app.system_audio import SystemAudio

MODEL = "gemini-3.5-live-translate-preview"
IN_RATE = 16000  # Gemini'nin beklediği giriş: 16 kHz, 16-bit, mono PCM
OUT_RATE = 24000  # Gemini'nin verdiği çıkış: 24 kHz, 16-bit, mono PCM
CHUNK_MS = 100
DIRECTIONS = {  # yön -> açılacak oturumların hedef dilleri
    "both": ("tr", "en"),
    "en-tr": ("tr",),
    "tr-en": ("en",),
}
RETRY_DELAYS = (1, 2, 4, 8, 15)  # art arda başarısız denemeler arasındaki bekleme (sn)
SILENCE_PEAK = 600  # bu genliğin altındaki ses "sessizlik" sayılır (int16 ölçeğinde)
UTTERANCE_GAP = 1.2  # bu kadar sn yeni metin gelmezse altyazı satırı tamamlanır
MAX_LINE_CHARS = 160  # ara vermeden süren konuşmada satır bu uzunluktan sonra cümle sonunda biter
SOURCE_GAP = 0.3  # bilgisayar sesi bu kadar sn veri vermezse sessizlik gönderilir
HOLD_AUDIO = 1.5  # dil belli olmadan en fazla bu kadar sn çeviri sesi bekletilir
SPOKEN_TTL = 3.0  # diğer oturumun dil tahmini bu kadar sn geçerli sayılır

TR_CHARS = set("çğıöşüÇĞİÖŞÜ")
TR_ALPHABET_EXTRA = TR_CHARS | set("âîûÂÎÛ")
TR_WORDS = {
    "ve", "bir", "bu", "şu", "ne", "de", "da", "mi", "mı", "mu", "ben", "sen", "biz", "siz",
    "için", "ama", "çok", "var", "yok", "evet", "hayır", "nasıl", "gibi", "daha", "şey",
    "neden", "şimdi", "tamam", "bence", "merhaba", "olur", "değil", "ile", "sonra", "kadar",
}
EN_WORDS = {
    "the", "and", "is", "are", "was", "you", "to", "of", "in", "it", "that", "what", "this",
    "i", "do", "have", "for", "with", "not", "can", "we", "my", "so", "be", "how", "yes",
    "no", "hello", "think", "just", "like", "will", "would", "your", "there", "okay",
}


def is_en_or_tr(text: str) -> bool:
    """Metin yalnızca İngilizce / Türkçe alfabesinin harflerinden mi oluşuyor?

    Gemini'nin konuşma tanıması dil ipucunu (`language_codes`) yok sayıyor ve
    Türkçeyi bazen Vietnamca gibi başka bir dil sanıp öyle yazıyor.
    """
    return all(ch.isascii() or ch in TR_ALPHABET_EXTRA for ch in text if ch.isalpha())


def detect_language(text: str) -> str | None:
    """Metnin Türkçe mi İngilizce mi olduğunu tahmin eder; emin değilse None."""
    if not is_en_or_tr(text):
        return "tr"  # yanlış tanınmış konuşma; İngilizcede bu harfler olmadığından Türkçedir
    words = re.findall(r"[^\W\d_]+", text.lower())
    tr = sum(ch in TR_CHARS for ch in text) + sum(w in TR_WORDS for w in words)
    en = sum(w in EN_WORDS for w in words)
    if tr == en:
        return None
    return "tr" if tr > en else "en"


def _peak(pcm: bytes) -> int:
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16)
    return int(np.abs(samples.astype(np.int32)).max()) if samples.size else 0


class FatalError(Exception):
    """Yeniden denemenin işe yaramayacağı hata (geçersiz anahtar, kota vb.)."""


def _fatal_reason(exc: BaseException) -> str | None:
    """Hata kalıcıysa kullanıcıya gösterilecek açıklamayı döndürür."""
    text = str(exc)
    lowered = text.lower()
    if "api key" in lowered or "api_key" in lowered or "permission" in lowered:
        return t("fatal_key")
    if "quota" in lowered or "resource_exhausted" in lowered or "rate limit" in lowered:
        return t("fatal_quota")
    if "not found" in lowered and "model" in lowered:
        return t("fatal_model")
    if isinstance(exc, errors.ClientError) and exc.code in (400, 401, 403, 404, 429):
        return t("fatal_rejected", error=text)
    return None


def list_devices() -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """(mikrofonlar, hoparlörler) -> [(cihaz no, ad), ...]

    Windows'ta aynı cihaz birden çok sürücü altında görünür; MME seçilir çünkü
    16 kHz / 24 kHz örnekleme hızlarını kendisi dönüştürür.
    """
    hostapis = sd.query_hostapis()
    preferred = next((i for i, h in enumerate(hostapis) if h["name"] == "MME"), None)
    inputs, outputs = [], []
    for i, dev in enumerate(sd.query_devices()):
        if preferred is not None and dev["hostapi"] != preferred:
            continue
        name = dev["name"]
        # Windows'un sanal "varsayılan cihaz" girdileri (arayüzde ayrıca sunuluyor).
        if any(s in name for s in ("Sound Mapper", "Ses Eşleştiricisi", "Primary Sound")):
            continue
        if dev["max_input_channels"] > 0:
            inputs.append((i, name))
        if dev["max_output_channels"] > 0:
            outputs.append((i, name))
    return inputs, outputs


class Player:
    """Oturumlardan gelen çeviri seslerini karıştırarak hoparlörden çalar.

    Gemini, konuşma olmasa da gerçek zamanlı sessizlik akıtır. Her oturumun
    kendi kuyruğu vardır ve kuyruklar arka arkaya değil üst üste (karıştırılarak)
    çalınır; böylece iki akış birbirinin gecikmesini büyütmez.

    `enabled=False` ise (yalnızca altyazı) hoparlör hiç açılmaz ve ses atılır.
    """

    MAX_QUEUED_SILENCE = int(OUT_RATE * 2 * 0.3)  # kuyruk bundan uzunsa sessizlik eklenmez

    def __init__(self, device: int | None = None, enabled: bool = True) -> None:
        self._bufs: dict[str, bytearray] = {}
        self._lock = threading.Lock()
        self._last_voice: dict[str, float] = {}
        self.enabled = enabled
        self.stream = (
            sd.RawOutputStream(
                samplerate=OUT_RATE, channels=1, dtype="int16", device=device,
                callback=self._callback,
            )
            if enabled
            else None
        )

    def _callback(self, outdata, frames, time_info, status) -> None:
        n = len(outdata)
        with self._lock:
            chunks = {src: bytes(buf[:n]) for src, buf in self._bufs.items() if buf}
            for buf in self._bufs.values():
                del buf[:n]
        if not chunks:
            outdata[:] = b"\x00" * n
            return
        now = time.monotonic()
        for src, chunk in chunks.items():
            if _peak(chunk) > SILENCE_PEAK:
                self._last_voice[src] = now
        total = np.zeros(n // 2, dtype=np.int32)
        for chunk in chunks.values():
            samples = np.frombuffer(chunk[: len(chunk) - len(chunk) % 2], dtype=np.int16)
            total[: samples.size] += samples
        outdata[:] = np.clip(total, -32768, 32767).astype(np.int16).tobytes()

    def play(self, source: str, pcm: bytes) -> None:
        if not self.enabled:
            return
        loud = _peak(pcm) > SILENCE_PEAK
        with self._lock:
            buf = self._bufs.setdefault(source, bytearray())
            # Kuyruk birikmişse sessizliği atla ki gecikme zamanla büyümesin.
            if not loud and len(buf) > self.MAX_QUEUED_SILENCE:
                return
            buf.extend(pcm)

    def is_speaking(self, source: str, tail: float = 0.6) -> bool:
        """Bu oturumun çevirisi hoparlörden az önce duyuldu mu? (`tail`: yankının sönmesi)"""
        return time.monotonic() - self._last_voice.get(source, 0.0) < tail


class LiveTranslator:
    """
    Geri çağrılar (farklı iş parçacıklarından çağrılabilir):
      on_text(satır_no, kaynak_dil, duyulan, hedef_dil, çeviri, bitti_mi)
                                Aynı satır_no ile tekrar tekrar çağrılır; metin
                                Gemini'den geldikçe büyür. Konuşmada
                                UTTERANCE_GAP kadar ara olunca bitti_mi=True olur.
      on_status(durum, mesaj)   durum: "connecting" | "live" | "reconnecting"
      on_level(0.0-1.0)         giriş sesi seviyesi
    Kalıcı bir hatada run() FatalError fırlatır.

    source: "mic" (input_device) ya da "system" (bilgisayarın çaldığı ses,
            Dialectra'nın kendi sesi hariç)
    direction: DIRECTIONS anahtarı ("both", "en-tr", "tr-en")
    speak: False ise çeviri seslendirilmez, yalnızca altyazı gelir.
    """

    def __init__(
        self,
        api_key: str | None = None,
        input_device: int | None = None,
        output_device: int | None = None,
        headphones: bool = False,
        source: str = "mic",
        direction: str = "both",
        speak: bool = True,
        on_text: Callable[[str, str, str, str], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
        on_level: Callable[[float], None] | None = None,
    ) -> None:
        self.client = genai.Client(api_key=api_key)
        self.input_device = input_device
        self.output_device = output_device
        self.headphones = headphones
        self.source = source
        self.targets = DIRECTIONS[direction]
        self.speak = speak
        self.on_text = on_text or (lambda *a: None)
        self.on_status = on_status or (lambda *a: None)
        self.on_level = on_level or (lambda *a: None)
        self.queues: dict[str, asyncio.Queue[bytes]] = {}
        self.states: dict[str, str] = {}
        self.utterance_count = 0
        self.spoken: tuple[str | None, float] = (None, 0.0)  # son tahmin edilen dil, zamanı

    def _config(self, target: str) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            # Not: bu model `language_codes` ipucunu yok sayıyor (denendi); yanlış
            # dilde yazıya dökülen konuşma `is_en_or_tr` ile ayıklanır.
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=target, echo_target_language=False
            ),
        )

    def _set_state(self, target: str, state: str, detail: str = "") -> None:
        self.states[target] = state
        states = set(self.states.values())
        if states == {"live"} and len(self.states) == len(self.targets):
            self.on_status("live", t("listening"))
        elif "reconnecting" in states:
            self.on_status("reconnecting", detail or t("reconnecting"))
        else:
            self.on_status("connecting", t("connecting"))

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self.queues = {t: asyncio.Queue(maxsize=50) for t in self.targets}
        self.states = {}
        player = Player(self.output_device, enabled=self.speak)
        silence = bytes(IN_RATE * CHUNK_MS // 1000 * 2)
        system = self.source == "system"
        # Bilgisayar sesi yakalanırken Dialectra'nın kendi sesi zaten hariç tutulur.
        echo_guard = self.speak and not system and not self.headphones
        last_audio = time.monotonic()

        def push(pcm: bytes) -> None:
            for target, q in self.queues.items():
                if q.full():  # oturum bağlı değilken eski sesi biriktirme
                    q.get_nowait()
                # Yankı döngüsünü önle: hoparlörden örneğin Türkçe çeviri çalarken
                # mikrofon bu Türkçeyi de duyar. "→ tr" oturumu Türkçeyi zaten yok
                # sayar; ama "→ en" oturumu onu geri çevirir. Bu yüzden yalnızca
                # *diğer* oturumun çevirisi çalarken bu oturuma sessizlik gönderilir.
                # Konuşan kişi hiç kesilmez.
                echo = echo_guard and any(
                    player.is_speaking(other) for other in self.targets if other != target
                )
                q.put_nowait(silence if echo else pcm)

        def on_audio(pcm: bytes) -> None:
            nonlocal last_audio
            last_audio = time.monotonic()
            push(pcm)

        def feed(pcm: bytes) -> None:  # ses iş parçacığından çağrılır
            self.on_level(min(1.0, _peak(pcm) / 20000))
            loop.call_soon_threadsafe(on_audio, pcm)

        async def fill_silence() -> None:
            # Windows, hiçbir şey çalmıyorken loopback verisi göndermez; oturumlar
            # sesin kesildiğini anlasın diye araya sessizlik konur.
            while True:
                await asyncio.sleep(CHUNK_MS / 1000)
                if time.monotonic() - last_audio > SOURCE_GAP:
                    self.on_level(0.0)
                    push(silence)

        if system:
            capture = SystemAudio(feed, IN_RATE, CHUNK_MS)
        else:
            capture = sd.RawInputStream(
                samplerate=IN_RATE,
                channels=1,
                dtype="int16",
                blocksize=IN_RATE * CHUNK_MS // 1000,
                device=self.input_device,
                callback=lambda indata, *_: feed(bytes(indata)),
            )
        self.on_status("connecting", t("connecting"))
        with capture, player.stream or contextlib.nullcontext():
            tasks = [asyncio.create_task(self._keep_session(t, player)) for t in self.targets]
            if system:
                tasks.append(asyncio.create_task(fill_silence()))
            try:
                # Biri kalıcı hatayla biterse hepsini durdur.
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _keep_session(self, target: str, player: Player) -> None:
        """Oturumu açık tutar; koparsa bekleyip yeniden bağlanır."""
        failures = 0
        while True:
            self._set_state(target, "connecting")
            started = time.monotonic()
            try:
                await self._session(target, player)
                reason = t("server_closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                fatal = _fatal_reason(exc)
                if fatal:
                    raise FatalError(fatal) from exc
                reason = t("connection_lost", error=type(exc).__name__)

            # Uzun süre çalıştıysa sayaç sıfırlanır; art arda hızlı kopmalarda bekleme uzar.
            failures = 0 if time.monotonic() - started > 30 else failures + 1
            if failures > len(RETRY_DELAYS):
                raise FatalError(t("cannot_connect"))
            delay = RETRY_DELAYS[min(failures, len(RETRY_DELAYS)) - 1] if failures else 0.5
            self._set_state(target, "reconnecting", reason)
            await asyncio.sleep(delay)

    async def _session(self, target: str, player: Player) -> None:
        queue = self.queues[target]
        source = "en" if target == "tr" else "tr"
        async with self.client.aio.live.connect(model=MODEL, config=self._config(target)) as session:
            while not queue.empty():  # bağlantı yokken biriken bayat sesi at
                queue.get_nowait()
            self._set_state(target, "live")

            async def send_audio() -> None:
                while True:
                    pcm = await queue.get()
                    await session.send_realtime_input(
                        audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={IN_RATE}")
                    )

            # Gemini metni parça parça akıtır ve "cümle bitti" sinyali göndermez.
            # Parçalar bir satırda birikir; konuşmada ara olunca satır tamamlanır.
            line = {"id": 0, "heard": "", "said": "", "last": 0.0}
            pending = bytearray()  # dil belli olana kadar bekletilen çeviri sesi
            held_since = 0.0

            # `echo_target_language=False` her zaman işe yaramıyor: Türkçe konuşulunca
            # "→ tr" oturumu da cümleyi Türkçe tekrar edebiliyor. Bu yüzden duyulan
            # metnin dili tahmin edilir; konuşma zaten bu oturumun hedef dilindeyse
            # sesi de altyazısı da atılır.
            def language() -> str | None:
                lang = detect_language(line["heard"])
                if lang:
                    self.spoken = (lang, time.monotonic())
                    return lang
                lang, at = self.spoken  # diğer oturum daha önce anlamış olabilir
                return lang if time.monotonic() - at < SPOKEN_TTL else None

            def play(pcm: bytes) -> None:
                nonlocal held_since
                lang = language()
                if lang == target:
                    pending.clear()
                    return
                now = time.monotonic()
                if lang is None and (pending or _peak(pcm) > SILENCE_PEAK):
                    if not pending:
                        held_since = now
                    if now - held_since < HOLD_AUDIO:
                        pending.extend(pcm)
                        return
                if pending:
                    player.play(target, bytes(pending))
                    pending.clear()
                player.play(target, pcm)

            def emit(final: bool) -> None:
                if not line["said"].strip():
                    return
                lang = language()
                if lang == target or (lang is None and not final):
                    return  # tekrar ise hiç, dil belirsizse satır bitince gösterilir
                heard = line["heard"].strip()
                if not is_en_or_tr(heard):
                    heard = ""  # yanlış dilde yazıya dökülmüş; hiç göstermemek daha iyi
                self.on_text(line["id"], source, heard, target, line["said"].strip(), final)

            def new_line() -> None:
                self.utterance_count += 1
                line.update(id=self.utterance_count, heard="", said="", last=0.0)

            async def finish_lines() -> None:
                while True:
                    await asyncio.sleep(0.2)
                    if line["last"] and time.monotonic() - line["last"] > UTTERANCE_GAP:
                        emit(final=True)
                        new_line()

            new_line()
            sender = asyncio.create_task(send_audio())
            finisher = asyncio.create_task(finish_lines())
            try:
                while True:
                    received = 0
                    async for response in session.receive():
                        received += 1
                        if response.go_away:  # sunucu oturumu birazdan kapatacak
                            return
                        sc = response.server_content
                        if not sc:
                            continue
                        changed = False
                        if sc.input_transcription and sc.input_transcription.text:
                            line["heard"] += sc.input_transcription.text
                            changed = True
                        if sc.output_transcription and sc.output_transcription.text:
                            line["said"] += sc.output_transcription.text
                            changed = True
                        if changed:
                            line["last"] = time.monotonic()
                            said = line["said"].rstrip()
                            if len(said) >= MAX_LINE_CHARS and said[-1] in ".?!…":
                                emit(final=True)
                                new_line()
                            else:
                                emit(final=False)
                        if sc.model_turn:
                            for part in sc.model_turn.parts or []:
                                if part.inline_data and part.inline_data.data:
                                    play(part.inline_data.data)
                    if sender.done():
                        sender.result()  # gönderimde hata varsa yukarı taşı
                    if not received:
                        raise ConnectionError("bağlantı kapandı")
            finally:
                emit(final=True)
                sender.cancel()
                finisher.cancel()
