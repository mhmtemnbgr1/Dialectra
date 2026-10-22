"""Canlı sesli çeviri: Gemini 3.5 Live Translate (İngilizce <-> Türkçe).

Mikrofon sesi aynı anda iki oturuma gönderilir: biri her şeyi Türkçeye,
diğeri İngilizceye çevirir. `echo_target_language=False` sayesinde zaten hedef
dilde olan konuşmada oturum susar; böylece hangi dilde konuşulursa konuşulsun
yalnızca doğru oturum ses üretir.

Her oturum bağımsızdır: biri koparsa yalnızca o yeniden bağlanır.
"""

import asyncio
import threading
import time
from array import array
from typing import Callable

import sounddevice as sd
from google import genai
from google.genai import errors, types

MODEL = "gemini-3.5-live-translate-preview"
IN_RATE = 16000  # Gemini'nin beklediği giriş: 16 kHz, 16-bit, mono PCM
OUT_RATE = 24000  # Gemini'nin verdiği çıkış: 24 kHz, 16-bit, mono PCM
CHUNK_MS = 100
TARGETS = ("tr", "en")
RETRY_DELAYS = (1, 2, 4, 8, 15)  # art arda başarısız denemeler arasındaki bekleme (sn)
SILENCE_PEAK = 600  # bu genliğin altındaki ses "sessizlik" sayılır (int16 ölçeğinde)
UTTERANCE_GAP = 1.2  # bu kadar sn yeni metin gelmezse altyazı satırı tamamlanır


def _peak(pcm: bytes) -> int:
    samples = array("h", pcm[: len(pcm) - len(pcm) % 2])
    return max(max(samples), -min(samples)) if samples else 0


class FatalError(Exception):
    """Yeniden denemenin işe yaramayacağı hata (geçersiz anahtar, kota vb.)."""


def _fatal_reason(exc: BaseException) -> str | None:
    """Hata kalıcıysa kullanıcıya gösterilecek açıklamayı döndürür."""
    text = str(exc)
    lowered = text.lower()
    if "api key" in lowered or "api_key" in lowered or "permission" in lowered:
        return "API anahtarı geçersiz ya da yetkisiz."
    if "quota" in lowered or "resource_exhausted" in lowered or "rate limit" in lowered:
        return "Ücretsiz kota doldu. Bir süre sonra tekrar dene."
    if "not found" in lowered and "model" in lowered:
        return "Model bulunamadı. Google modeli kaldırmış ya da adını değiştirmiş olabilir."
    if isinstance(exc, errors.ClientError) and exc.code in (400, 401, 403, 404, 429):
        return f"Gemini isteği reddetti: {text}"
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
    """

    MAX_QUEUED_SILENCE = int(OUT_RATE * 2 * 0.3)  # kuyruk bundan uzunsa sessizlik eklenmez

    def __init__(self, device: int | None = None) -> None:
        self._bufs: dict[str, bytearray] = {}
        self._lock = threading.Lock()
        self._last_voice: dict[str, float] = {}
        self.stream = sd.RawOutputStream(
            samplerate=OUT_RATE, channels=1, dtype="int16", device=device, callback=self._callback
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
        if len(chunks) == 1:
            mixed = next(iter(chunks.values()))
        else:
            total = array("h", bytes(n))
            for chunk in chunks.values():
                for i, v in enumerate(array("h", chunk)):
                    total[i] = max(-32768, min(32767, total[i] + v))
            mixed = total.tobytes()
        outdata[: len(mixed)] = mixed
        outdata[len(mixed) :] = b"\x00" * (n - len(mixed))

    def play(self, source: str, pcm: bytes) -> None:
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
      on_level(0.0-1.0)         mikrofon seviyesi
    Kalıcı bir hatada run() FatalError fırlatır.
    """

    def __init__(
        self,
        api_key: str | None = None,
        input_device: int | None = None,
        output_device: int | None = None,
        headphones: bool = False,
        on_text: Callable[[str, str, str, str], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
        on_level: Callable[[float], None] | None = None,
    ) -> None:
        self.client = genai.Client(api_key=api_key)
        self.input_device = input_device
        self.output_device = output_device
        self.headphones = headphones
        self.on_text = on_text or (lambda *a: None)
        self.on_status = on_status or (lambda *a: None)
        self.on_level = on_level or (lambda *a: None)
        self.queues: dict[str, asyncio.Queue[bytes]] = {}
        self.states: dict[str, str] = {}
        self.utterance_count = 0

    def _config(self, target: str) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=target, echo_target_language=False
            ),
        )

    def _set_state(self, target: str, state: str, detail: str = "") -> None:
        self.states[target] = state
        states = set(self.states.values())
        if states == {"live"} and len(self.states) == len(TARGETS):
            self.on_status("live", "Dinliyor")
        elif "reconnecting" in states:
            self.on_status("reconnecting", detail or "Bağlantı koptu, yeniden bağlanıyor...")
        else:
            self.on_status("connecting", "Bağlanıyor...")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self.queues = {t: asyncio.Queue(maxsize=50) for t in TARGETS}
        self.states = {}
        player = Player(self.output_device)
        silence = bytes(IN_RATE * CHUNK_MS // 1000 * 2)

        def push(pcm: bytes) -> None:
            for target, q in self.queues.items():
                if q.full():  # oturum bağlı değilken eski sesi biriktirme
                    q.get_nowait()
                # Yankı döngüsünü önle: hoparlörden örneğin Türkçe çeviri çalarken
                # mikrofon bu Türkçeyi de duyar. "→ tr" oturumu Türkçeyi zaten yok
                # sayar; ama "→ en" oturumu onu geri çevirir. Bu yüzden yalnızca
                # *diğer* oturumun çevirisi çalarken bu oturuma sessizlik gönderilir.
                # Konuşan kişi hiç kesilmez.
                echo = not self.headphones and any(
                    player.is_speaking(other) for other in TARGETS if other != target
                )
                q.put_nowait(silence if echo else pcm)

        def mic_callback(indata, frames, time_info, status) -> None:
            pcm = bytes(indata)
            self.on_level(min(1.0, _peak(pcm) / 20000))
            loop.call_soon_threadsafe(push, pcm)

        mic = sd.RawInputStream(
            samplerate=IN_RATE,
            channels=1,
            dtype="int16",
            blocksize=IN_RATE * CHUNK_MS // 1000,
            device=self.input_device,
            callback=mic_callback,
        )
        self.on_status("connecting", "Bağlanıyor...")
        with mic, player.stream:
            tasks = [asyncio.create_task(self._keep_session(t, player)) for t in TARGETS]
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
                reason = "Sunucu oturumu kapattı, yeniden bağlanıyor..."
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                fatal = _fatal_reason(exc)
                if fatal:
                    raise FatalError(fatal) from exc
                reason = f"Bağlantı koptu ({type(exc).__name__}), yeniden bağlanıyor..."

            # Uzun süre çalıştıysa sayaç sıfırlanır; art arda hızlı kopmalarda bekleme uzar.
            failures = 0 if time.monotonic() - started > 30 else failures + 1
            if failures > len(RETRY_DELAYS):
                raise FatalError("Gemini'ye bağlanılamıyor. İnternet bağlantını kontrol et.")
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

            def emit(final: bool) -> None:
                if line["said"].strip():
                    self.on_text(
                        line["id"], source, line["heard"].strip(), target, line["said"].strip(), final
                    )

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
                            emit(final=False)
                        if sc.model_turn:
                            for part in sc.model_turn.parts or []:
                                if part.inline_data and part.inline_data.data:
                                    player.play(target, part.inline_data.data)
                    if sender.done():
                        sender.result()  # gönderimde hata varsa yukarı taşı
                    if not received:
                        raise ConnectionError("bağlantı kapandı")
            finally:
                emit(final=True)
                sender.cancel()
                finisher.cancel()
