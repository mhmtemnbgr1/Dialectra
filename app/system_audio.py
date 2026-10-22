"""Bilgisayarın çaldığı sesi yakalama (Windows "process loopback").

Windows 10 2004 ve sonrası, belirli bir işlem *dışındaki* bütün sesi kaydetmeye
izin verir. Dialectra kendi işlemini hariç tutar: YouTube, Zoom, dizi... duyulur,
ama hoparlörden çalan çeviri geri dönüp tekrar tekrar çevrilmez.

Ek paket gerekmez; Windows'un ses API'si (WASAPI) ctypes ile çağrılır. Windows
sesi istenen biçime (16 kHz, 16-bit, mono) kendisi dönüştürür.
"""

import ctypes
import os
import threading
import uuid
from ctypes import POINTER, WINFUNCTYPE, Structure, byref, c_long, c_ulong, c_ushort, c_void_p
from ctypes import wintypes
from typing import Callable

from app.i18n import t

AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1
VT_BLOB = 65
E_NOINTERFACE = -2147467262
WAVE_FORMAT_PCM = 1
BUFFER_100NS = 2_000_000  # 200 ms
_KEEP_ALIVE: list = []

# COM nesnelerinde yöntemlerin sanal tablo sırası
RELEASE = 2
GET_ACTIVATE_RESULT = 3
CLIENT_INITIALIZE, CLIENT_START, CLIENT_STOP, CLIENT_SET_EVENT, CLIENT_GET_SERVICE = 3, 10, 11, 13, 14
CAPTURE_GET_BUFFER, CAPTURE_RELEASE_BUFFER, CAPTURE_NEXT_PACKET = 3, 4, 5


class AudioDeviceError(Exception):
    """Ses cihazı açılamadı."""


class GUID(Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        guid = cls()
        ctypes.memmove(guid.data, uuid.UUID(text).bytes_le, 16)
        return guid


IID_IUNKNOWN = GUID.of("00000000-0000-0000-C000-000000000046")
IID_IAGILE_OBJECT = GUID.of("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_COMPLETION_HANDLER = GUID.of("41D949AB-9862-444A-80F6-C261334DA5EB")
IID_IAUDIO_CLIENT = GUID.of("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAUDIO_CAPTURE_CLIENT = GUID.of("C8ADBD64-E71E-48a0-A4DE-185C395CD317")


class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class ACTIVATION_PARAMS(Structure):  # AUDIOCLIENT_ACTIVATION_PARAMS
    _fields_ = [("ActivationType", c_long), ("TargetProcessId", wintypes.DWORD),
                ("ProcessLoopbackMode", c_long)]


class BLOB(Structure):
    _fields_ = [("cbSize", c_ulong), ("pBlobData", c_void_p)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                ("blob", BLOB)]


QueryInterface = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
AddRefRelease = WINFUNCTYPE(c_ulong, c_void_p)
ActivateCompleted = WINFUNCTYPE(c_long, c_void_p, c_void_p)


class HandlerVtbl(Structure):
    _fields_ = [("QueryInterface", QueryInterface), ("AddRef", AddRefRelease),
                ("Release", AddRefRelease), ("ActivateCompleted", ActivateCompleted)]


class Handler(Structure):  # IActivateAudioInterfaceCompletionHandler
    _fields_ = [("lpVtbl", POINTER(HandlerVtbl))]


def _method(obj: c_void_p, index: int, *argtypes):
    """COM nesnesinin `index`. yöntemini çağrılabilir hale getirir (HRESULT hata fırlatır)."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    fn = WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtbl[index])
    return lambda *args: fn(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj:
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        WINFUNCTYPE(c_ulong, c_void_p)(vtbl[RELEASE])(obj)


def _activate_client() -> c_void_p:
    """Bu işlem dışındaki tüm sesi yakalayan bir IAudioClient döndürür."""
    done = threading.Event()
    accepted = {bytes(i.data) for i in (IID_IUNKNOWN, IID_IAGILE_OBJECT, IID_COMPLETION_HANDLER)}

    def query(this, iid, out):
        if bytes(iid.contents.data) in accepted:
            out[0] = this
            return 0
        out[0] = None
        return E_NOINTERFACE

    def completed(this, operation):
        done.set()
        return 0

    callbacks = (QueryInterface(query), AddRefRelease(lambda this: 1),
                 AddRefRelease(lambda this: 1), ActivateCompleted(completed))
    vtbl = HandlerVtbl(*callbacks)
    handler = Handler(ctypes.pointer(vtbl))

    params = ACTIVATION_PARAMS(ACTIVATION_TYPE_PROCESS_LOOPBACK, os.getpid(),
                               LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE)
    prop = PROPVARIANT(vt=VT_BLOB, blob=BLOB(ctypes.sizeof(params),
                                             ctypes.cast(byref(params), c_void_p)))
    operation = c_void_p()
    mmdevapi = ctypes.WinDLL("mmdevapi")
    mmdevapi.ActivateAudioInterfaceAsync.restype = ctypes.HRESULT
    mmdevapi.ActivateAudioInterfaceAsync(
        ctypes.c_wchar_p("VAD\\Process_Loopback"), byref(IID_IAUDIO_CLIENT), byref(prop),
        byref(handler), byref(operation),
    )
    try:
        if not done.wait(timeout=5):
            raise AudioDeviceError(t("capture_timeout"))
        result, client = c_long(), c_void_p()
        _method(operation, GET_ACTIVATE_RESULT, POINTER(c_long), POINTER(c_void_p))(
            byref(result), byref(client)
        )
        if result.value < 0:
            raise ctypes.WinError(result.value)
        return client
    finally:
        _release(operation)
        # Windows işleyiciyi sonradan da çağırabilir (Release); bellekte kalmalı.
        _KEEP_ALIVE.append((callbacks, vtbl, handler))


class SystemAudio:
    """Bilgisayarın çaldığı sesi (Dialectra'nınki hariç) `chunk_ms` parçalar halinde
    `on_chunk`'a verir. Hiçbir şey çalmıyorken Windows veri göndermeyebilir.
    """

    def __init__(self, on_chunk: Callable[[bytes], None], rate: int, chunk_ms: int) -> None:
        self.on_chunk = on_chunk
        self.rate = rate
        self.chunk_bytes = rate * chunk_ms // 1000 * 2
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "SystemAudio":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        if self._error is not None:
            raise AudioDeviceError(t("capture_failed", error=self._error)) from self._error
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        ole32, kernel32 = ctypes.windll.ole32, ctypes.windll.kernel32
        ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        client = capture = c_void_p()
        event = kernel32.CreateEventW(None, False, False, None)
        try:
            try:
                client = _activate_client()
                fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, 1, self.rate, self.rate * 2, 2, 16, 0)
                _method(client, CLIENT_INITIALIZE, ctypes.c_int, wintypes.DWORD, ctypes.c_longlong,
                        ctypes.c_longlong, POINTER(WAVEFORMATEX), c_void_p)(
                    0,
                    AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                    | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                    BUFFER_100NS, 0, byref(fmt), None,
                )
                _method(client, CLIENT_SET_EVENT, wintypes.HANDLE)(event)
                capture = c_void_p()
                _method(client, CLIENT_GET_SERVICE, POINTER(GUID), POINTER(c_void_p))(
                    byref(IID_IAUDIO_CAPTURE_CLIENT), byref(capture)
                )
                _method(client, CLIENT_START)()
            except (OSError, AudioDeviceError) as exc:
                self._error = exc
                return
            finally:
                self._ready.set()
            self._capture_loop(capture, event)
            _method(client, CLIENT_STOP)()
        finally:
            _release(capture)
            _release(client)
            kernel32.CloseHandle(event)
            ole32.CoUninitialize()

    def _capture_loop(self, capture: c_void_p, event) -> None:
        kernel32 = ctypes.windll.kernel32
        next_packet = _method(capture, CAPTURE_NEXT_PACKET, POINTER(ctypes.c_uint32))
        get_buffer = _method(capture, CAPTURE_GET_BUFFER, POINTER(c_void_p),
                             POINTER(ctypes.c_uint32), POINTER(wintypes.DWORD), c_void_p, c_void_p)
        release_buffer = _method(capture, CAPTURE_RELEASE_BUFFER, ctypes.c_uint32)
        size, data, frames, flags = ctypes.c_uint32(), c_void_p(), ctypes.c_uint32(), wintypes.DWORD()
        pending = bytearray()
        while not self._stop.is_set():
            kernel32.WaitForSingleObject(event, 100)
            next_packet(byref(size))
            while size.value:
                get_buffer(byref(data), byref(frames), byref(flags), None, None)
                n = frames.value * 2
                if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data.value:
                    pending.extend(bytes(n))
                else:
                    pending.extend(ctypes.string_at(data.value, n))
                release_buffer(frames.value)
                next_packet(byref(size))
            while len(pending) >= self.chunk_bytes:
                self.on_chunk(bytes(pending[: self.chunk_bytes]))
                del pending[: self.chunk_bytes]
