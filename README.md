# Dialectra

**Türkçe** | [English](README.en.md)

Windows için İngilizce ↔ Türkçe canlı sesli çeviri. Mikrofona konuşulanı ya da
bilgisayarda çalan sesi (YouTube, toplantı, dizi) çevirir; çevirisi hoparlörden
çalar ve ekranda altyazı olarak görünür. Çeviri
[Gemini 3.5 Live Translate](https://ai.google.dev/gemini-api/docs/live-api/live-translate)
ile yapılır.

<img src="docs/images/main-window.png" alt="Dialectra ana pencere" width="720">

## Kullanım

1. [Google AI Studio](https://aistudio.google.com/apikey)'dan ücretsiz API anahtarı al.
2. `Dialectra.exe`'yi aç, **API anahtarı...** düğmesiyle anahtarı kaydet.
3. **Başlat**'a bas ve konuş.

<img src="docs/images/api-key-dialog.png" alt="API anahtarı penceresi" width="420">

## Seçenekler

- **Ne çevrilsin?** Mikrofon ya da bilgisayar sesi. Bilgisayar sesinde Dialectra'nın
  kendi sesi yakalanmaz, çeviri aynı kulaklıktan çalabilir (Windows 10 2004 ve üstü).
- **Yön:** İki yön ya da yalnızca EN → TR / TR → EN. Tek yön, ücretsiz kotanın yarısını harcar.
- **Kulaklık kullanıyorum:** Kulaklık yoksa hoparlörden çıkan çevirinin mikrofona geri
  dönüp tekrar çevrilmesi engellenir.
- **Çeviriyi seslendir:** Kapalıyken yalnızca altyazı gösterilir.
- **Altyazı penceresi:** Her zaman üstte duran pencere. Sürükleyerek taşınır, fare
  tekerleğiyle yazı boyutu değişir.
- **Arayüz dili:** Türkçe ya da English.
- **Ctrl+Alt+D:** Uygulama arka plandayken başlat / durdur.

## Bilinen sınırlamalar

- Gemini çeviriyi konuşanın sesine benzeterek okur ve ses seçmeye izin vermez; bu
  yüzden ses zaman zaman kadından erkeğe (ya da tersi) geçebilir.
- Gemini konuşmayı bazen yanlış dilde yazıya döker. Dialectra bu satırları gizler;
  çeviri bundan etkilenmez.

## Geliştirme

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python dialectra.py
```

Testler:

```bash
.venv\Scripts\python -m unittest discover tests
```

`.exe` oluşturma:

```bash
.venv\Scripts\pyinstaller dialectra.py --noconfirm --onefile --windowed --name Dialectra --workpath .build --distpath dist --specpath .build
```

`v` ile başlayan bir etiket (ör. `v1.1.0`) push'lanınca GitHub Actions `.exe`'yi
derleyip Release'e yükler. Kullanılan paketlerin lisansları Release'teki
`THIRD_PARTY_LICENSES.txt` dosyasındadır.
