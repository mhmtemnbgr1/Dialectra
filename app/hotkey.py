"""Uygulama arka plandayken de çalışan kısayol tuşu (Windows RegisterHotKey).

Windows'a yalnızca tek bir tuş birleşimi kaydedilir; diğer tuş basışları
uygulamaya hiç ulaşmaz. Ek paket gerekmez.
"""

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000  # basılı tutunca tekrar tekrar tetiklenmesin
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 1


class GlobalHotkey:
    """`modifiers` + `key` basılınca `callback`'i (kendi iş parçacığından) çağırır."""

    def __init__(self, key: str, callback: Callable[[], None],
                 modifiers: int = MOD_CONTROL | MOD_ALT) -> None:
        self.vk = ord(key.upper())
        self.modifiers = modifiers
        self.callback = callback
        self._thread_id = 0
        self._ok = False

    def start(self) -> bool:
        """Kısayolu kaydeder. Başka bir uygulama kullanıyorsa False döner."""
        if sys.platform != "win32":
            return False
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True).start()
        ready.wait(timeout=2)
        return self._ok

    def _run(self, ready: threading.Event) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        # Pencere tutamacı yerine None: mesajlar bu iş parçacığının kuyruğuna gelir.
        self._ok = bool(user32.RegisterHotKey(None, HOTKEY_ID, self.modifiers | MOD_NOREPEAT,
                                              self.vk))
        ready.set()
        if not self._ok:
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self) -> None:
        if self._ok and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
