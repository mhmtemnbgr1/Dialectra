"""Dialectra.exe'nin içine giren paketlerin lisans metinlerini tek dosyada toplar.

Apache-2.0, MIT ve BSD lisansları, paket dağıtılırken lisans metninin de
verilmesini ister. Bu betik requirements.txt'den başlayıp tüm bağımlılıkları
dolaşır; derleme araçları (PyInstaller vb.) dahil edilmez.

    python scripts/licenses.py THIRD_PARTY_LICENSES.txt
"""

import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
LICENSE_FILE = re.compile(r"(LICEN[CS]E|COPYING|NOTICE)", re.IGNORECASE)


def runtime_distributions() -> list[metadata.Distribution]:
    pending = [
        Requirement(line).name
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    seen: dict[str, metadata.Distribution] = {}
    while pending:
        name = pending.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:  # yalnızca başka bir platformda gerekli
            continue
        seen[key] = dist
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                pending.append(req.name)
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "THIRD_PARTY_LICENSES.txt")
    parts = ["Dialectra.exe şu açık kaynak paketleri içerir.\n"]
    dists = runtime_distributions()
    for dist in dists:
        meta = dist.metadata
        license_name = meta.get("License-Expression") or meta.get("License") or "?"
        parts.append(f"\n{'=' * 78}\n{meta['Name']} {dist.version}  ({license_name.splitlines()[0]})\n")
        for file in dist.files or []:
            if LICENSE_FILE.search(file.name):
                text = file.locate().read_text(encoding="utf-8", errors="replace")
                parts.append(f"\n--- {file} ---\n{text}")
    out.write_text("".join(parts), encoding="utf-8")
    print(f"{out}: {len(dists)} paket")


if __name__ == "__main__":
    main()
