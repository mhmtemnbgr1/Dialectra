# Dialectra

**Türkçe** | [English](README.en.md)

İngilizce ↔ Türkçe canlı sesli çeviri uygulaması (Windows). Mikrofona İngilizce
ya da Türkçe konuş; çevirisi hoparlörden çalar ve ekranda altyazı olarak görünür.
Çeviri [Gemini 3.5 Live Translate](https://ai.google.dev/gemini-api/docs/live-api/live-translate)
ile yapılır.

<img src="docs/images/main-window.png" alt="Dialectra ana pencere" width="720">

## Kullanım

1. [Google AI Studio](https://aistudio.google.com/apikey)'dan ücretsiz API anahtarı al.
2. `Dialectra.exe`'yi aç, **API anahtarı** düğmesiyle anahtarı kaydet.
3. **Başlat**'a bas ve konuş.

<img src="docs/images/api-key-dialog.png" alt="API anahtarı penceresi" width="400">

## Geliştirme

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python dialectra.py
```

`.exe` oluşturma:

```bash
.venv\Scripts\pyinstaller dialectra.py --noconfirm --onefile --windowed --name Dialectra --collect-data customtkinter --workpath .build --distpath dist --specpath .build
```
