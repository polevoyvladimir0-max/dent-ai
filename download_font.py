import io
import zipfile
import urllib.request
from pathlib import Path

FONT_URL = "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.zip"
TARGET_DIR = Path(r"C:/dent_ai/fonts")
TARGET_DIR.mkdir(parents=True, exist_ok=True)
FILES = {
    "DejaVuSans.ttf": "dejavu-fonts-ttf-2.37/ttf/DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf": "dejavu-fonts-ttf-2.37/ttf/DejaVuSans-Bold.ttf",
    "DejaVuSans-Oblique.ttf": "dejavu-fonts-ttf-2.37/ttf/DejaVuSans-Oblique.ttf",
    "DejaVuSans-BoldOblique.ttf": "dejavu-fonts-ttf-2.37/ttf/DejaVuSans-BoldOblique.ttf",
}

print("Downloading", FONT_URL)
with urllib.request.urlopen(FONT_URL) as resp:
    data = resp.read()

with zipfile.ZipFile(io.BytesIO(data)) as zf:
    for filename, member in FILES.items():
        with zf.open(member) as src, open(TARGET_DIR / filename, "wb") as dst:
            dst.write(src.read())
            print("Saved", TARGET_DIR / filename)
