"""Canlı sesli çeviri, arayüzsüz (komut satırı) sürüm.

Mikrofona İngilizce ya da Türkçe konuş; diğer dile çevrilmiş hali hoparlörden
çalar. Durdurmak için Ctrl+C. Arayüzlü sürüm için: python dialectra.py

    python scripts/live.py
    python scripts/live.py --headphones   # kulaklıkla: mikrofon hiç susturulmaz
    python scripts/live.py --system --direction en-tr --mute
                                          # bilgisayar sesini yalnızca Türkçe altyazıya çevir
    python scripts/live.py --devices      # ses cihazlarını listele
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import settings  # noqa: E402
from app.live_translate import DIRECTIONS, FatalError, LiveTranslator, list_devices  # noqa: E402
from app.system_audio import AudioDeviceError  # noqa: E402


def show_text(line_id: int, source: str, heard: str, target: str, said: str, final: bool) -> None:
    if not final:  # komut satırında yalnızca tamamlanmış satırları yaz
        return
    if heard:
        print(f"[{source.upper()}] {heard}")
    print(f"[{target.upper()}] {said}\n")


def show_status(state: str, message: str) -> None:
    print(f"-- {message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headphones", action="store_true", help="Kulaklık kullanılıyor")
    parser.add_argument("--devices", action="store_true", help="Ses cihazlarını listele")
    parser.add_argument("--input", type=int, help="Mikrofon cihaz numarası")
    parser.add_argument("--output", type=int, help="Hoparlör cihaz numarası")
    parser.add_argument("--system", action="store_true",
                        help="Mikrofon yerine bilgisayar sesi (Dialectra hariç)")
    parser.add_argument("--direction", choices=DIRECTIONS, default="both", help="Çeviri yönü")
    parser.add_argument("--mute", action="store_true", help="Seslendirme yok, yalnızca metin")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.devices:
        inputs, outputs = list_devices()
        print("Mikrofonlar:", *(f"  {i}: {n}" for i, n in inputs), sep="\n")
        print("Hoparlörler:", *(f"  {i}: {n}" for i, n in outputs), sep="\n")
        return

    api_key = settings.load_api_key()
    if not api_key:
        sys.exit("GEMINI_API_KEY bulunamadı. Proje klasöründeki .env dosyasına anahtarını yaz.")

    translator = LiveTranslator(
        api_key=api_key,
        input_device=args.input,
        output_device=args.output,
        headphones=args.headphones,
        source="system" if args.system else "mic",
        direction=args.direction,
        speak=not args.mute,
        on_text=show_text,
        on_status=show_status,
    )
    print("İngilizce ya da Türkçe konuş. (Durdurmak için Ctrl+C)\n")
    try:
        asyncio.run(translator.run())
    except KeyboardInterrupt:
        print("\nDurduruldu.")
    except (FatalError, AudioDeviceError) as exc:
        sys.exit(f"Hata: {exc}")


if __name__ == "__main__":
    main()
