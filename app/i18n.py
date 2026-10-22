"""Arayüz metinleri (Türkçe / İngilizce).

`t("anahtar", ad=değer)` seçili dildeki metni döndürür. Dil değişince arayüz
yeniden kurulur; motorun durum ve hata mesajları da buradan gelir.
"""

import ctypes
import sys

LANGUAGES = {"tr": "Türkçe", "en": "English"}

STRINGS = {
    "tr": {
        "subtitle": "İngilizce ↔ Türkçe canlı sesli çeviri",
        "overlay_button": "Altyazı penceresi",
        "key_button": "API anahtarı...",
        "ui_language": "Arayüz dili:",
        "group_source": "Ne çevrilsin?",
        "source_mic": "Mikrofon",
        "source_system": "Bilgisayar sesi",
        "group_direction": "Yön",
        "direction_both": "İki yön",
        "group_devices": "Ses cihazları",
        "mic": "Mikrofon:",
        "listening_to": "Dinlenen:",
        "speaker": "Hoparlör:",
        "default_device": "Sistem varsayılanı",
        "all_audio": "Bilgisayardaki tüm sesler (Dialectra hariç)",
        "headphones": "Kulaklık kullanıyorum",
        "headphones_tip": "Kulaklık yoksa, hoparlörden çıkan çevirinin mikrofona geri dönüp "
                          "tekrar çevrilmesi engellenir. Kulaklıkla buna gerek yok.",
        "speak": "Çeviriyi seslendir",
        "clear": "Temizle",
        "start": "Başlat",
        "stop": "Durdur",
        "volume": "Ses",
        "translating": "  çevriliyor...",
        "placeholder_title": "Çeviriler burada görünecek",
        "placeholder": "İngilizce konuşursan Türkçe, Türkçe konuşursan İngilizce duyarsın.\n"
                       "Dil seçmene gerek yok.",
        "placeholder_hotkey": "Uygulama arka plandayken {hotkey} ile başlat / durdur.",
        "overlay_title": "Altyazı",
        "overlay_empty": "Altyazılar burada görünecek",
        "state_idle": "Hazır",
        "state_connecting": "Bağlanıyor",
        "state_reconnecting": "Uyarı",
        "state_live": "Canlı",
        "state_error": "Hata",
        "status_ready": "Başlamak için 'Başlat'a bas.",
        "status_connecting_gemini": "Gemini'ye bağlanıyor...",
        "status_stopping": "Durduruluyor...",
        "status_stopped": "Durduruldu.",
        "status_listening_again": "Dinliyor — konuşmaya başlayabilirsin.",
        "status_mic_silent": "Mikrofondan ses gelmiyor. Windows'ta sessize alınmış ya da "
                             "seviyesi sıfır olabilir.",
        "error_device": "Ses cihazı açılamadı: {error}",
        "error_unexpected": "Beklenmeyen hata: {error}",
        "key_title": "Gemini API anahtarı",
        "key_info": "Anahtar yalnızca bu bilgisayarda, uygulama klasöründeki .env dosyasında "
                    "saklanır.",
        "key_link": "Ücretsiz anahtar al: aistudio.google.com/apikey",
        "key_label": "Anahtar:",
        "key_show": "Anahtarı göster",
        "ok": "Tamam",
        "cancel": "İptal",
        # motor
        "connecting": "Bağlanıyor...",
        "listening": "Dinliyor",
        "reconnecting": "Bağlantı koptu, yeniden bağlanıyor...",
        "server_closed": "Sunucu oturumu kapattı, yeniden bağlanıyor...",
        "connection_lost": "Bağlantı koptu ({error}), yeniden bağlanıyor...",
        "cannot_connect": "Gemini'ye bağlanılamıyor. İnternet bağlantını kontrol et.",
        "fatal_key": "API anahtarı geçersiz ya da yetkisiz.",
        "fatal_quota": "Ücretsiz kota doldu. Bir süre sonra tekrar dene.",
        "fatal_model": "Model bulunamadı. Google modeli kaldırmış ya da adını değiştirmiş "
                       "olabilir.",
        "fatal_rejected": "Gemini isteği reddetti: {error}",
        "capture_timeout": "Windows ses yakalamayı başlatmadı (zaman aşımı).",
        "capture_failed": "Bilgisayar sesi yakalanamadı: {error}",
    },
    "en": {
        "subtitle": "Live English ↔ Turkish voice translation",
        "overlay_button": "Subtitle window",
        "key_button": "API key...",
        "ui_language": "Interface language:",
        "group_source": "What to translate?",
        "source_mic": "Microphone",
        "source_system": "Computer audio",
        "group_direction": "Direction",
        "direction_both": "Both ways",
        "group_devices": "Audio devices",
        "mic": "Microphone:",
        "listening_to": "Listening to:",
        "speaker": "Speaker:",
        "default_device": "System default",
        "all_audio": "All computer audio (except Dialectra)",
        "headphones": "I'm using headphones",
        "headphones_tip": "Without headphones, the translation coming out of the speakers is "
                          "kept from looping back into the microphone. Headphones don't need this.",
        "speak": "Speak translation",
        "clear": "Clear",
        "start": "Start",
        "stop": "Stop",
        "volume": "Level",
        "translating": "  translating...",
        "placeholder_title": "Translations will appear here",
        "placeholder": "Speak English and you'll hear Turkish; speak Turkish and you'll hear "
                       "English.\nNo need to pick a language.",
        "placeholder_hotkey": "Press {hotkey} to start / stop while the app is in the background.",
        "overlay_title": "Subtitles",
        "overlay_empty": "Subtitles will appear here",
        "state_idle": "Ready",
        "state_connecting": "Connecting",
        "state_reconnecting": "Warning",
        "state_live": "Live",
        "state_error": "Error",
        "status_ready": "Press 'Start' to begin.",
        "status_connecting_gemini": "Connecting to Gemini...",
        "status_stopping": "Stopping...",
        "status_stopped": "Stopped.",
        "status_listening_again": "Listening — you can start speaking.",
        "status_mic_silent": "No sound from the microphone. It may be muted or set to zero "
                             "in Windows.",
        "error_device": "Could not open the audio device: {error}",
        "error_unexpected": "Unexpected error: {error}",
        "key_title": "Gemini API key",
        "key_info": "The key is stored only on this computer, in the .env file in the app "
                    "folder.",
        "key_link": "Get a free key: aistudio.google.com/apikey",
        "key_label": "Key:",
        "key_show": "Show key",
        "ok": "OK",
        "cancel": "Cancel",
        # engine
        "connecting": "Connecting...",
        "listening": "Listening",
        "reconnecting": "Connection lost, reconnecting...",
        "server_closed": "The server closed the session, reconnecting...",
        "connection_lost": "Connection lost ({error}), reconnecting...",
        "cannot_connect": "Can't reach Gemini. Check your internet connection.",
        "fatal_key": "The API key is invalid or not authorized.",
        "fatal_quota": "The free quota is used up. Try again later.",
        "fatal_model": "Model not found. Google may have removed or renamed it.",
        "fatal_rejected": "Gemini rejected the request: {error}",
        "capture_timeout": "Windows did not start audio capture (timed out).",
        "capture_failed": "Could not capture computer audio: {error}",
    },
}

_language = "tr"


def system_language() -> str:
    """Windows arayüzü Türkçeyse "tr", değilse "en"."""
    if sys.platform == "win32":
        try:
            return "tr" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x1F else "en"
        except (AttributeError, OSError):
            pass
    return "tr"


def set_language(language: str) -> None:
    global _language
    _language = language if language in STRINGS else "tr"


def language() -> str:
    return _language


def t(key: str, **kwargs) -> str:
    text = STRINGS[_language][key]
    return text.format(**kwargs) if kwargs else text
