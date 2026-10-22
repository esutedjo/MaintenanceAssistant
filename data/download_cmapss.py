"""Download NASA C-MAPSS and extract FD001/FD003 into data/raw/cmapss. Run: uv run task download"""

import hashlib
import io
import ssl
import sys
import zipfile
from pathlib import Path

import httpx
import truststore

SOURCES = [
    "https://zenodo.org/records/15346912/files/CMAPSSData.zip?download=1",
    "https://data.nasa.gov/docs/legacy/CMAPSSData.zip",
]
DATASETS = ("FD001", "FD003")
RAW_DIR = Path(__file__).resolve().parent / "raw" / "cmapss"


def _fetch() -> bytes:
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for url in SOURCES:
        try:
            response = httpx.get(url, verify=ctx, follow_redirects=True, timeout=120)
            response.raise_for_status()
            zipfile.ZipFile(io.BytesIO(response.content)).testzip()
            print(f"downloaded {len(response.content):,} bytes from {url}")
            return response.content
        except (httpx.HTTPError, zipfile.BadZipFile) as error:
            print(f"failed {url}: {error}", file=sys.stderr)
    raise SystemExit("No C-MAPSS source reachable.")


def main() -> int:
    wanted = {f"{split}_{ds}.txt" for ds in DATASETS for split in ("train", "test", "RUL")}
    if all((RAW_DIR / name).exists() for name in wanted):
        print(f"already present in {RAW_DIR}")
        return 0

    archive = zipfile.ZipFile(io.BytesIO(_fetch()))
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for info in archive.infolist():
        name = Path(info.filename).name
        if name in wanted:
            content = archive.read(info)
            (RAW_DIR / name).write_bytes(content)
            print(f"{name:16} {hashlib.sha256(content).hexdigest()[:16]}  {len(content):,} bytes")
            wanted.discard(name)
    if wanted:
        raise SystemExit(f"Missing from archive: {sorted(wanted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
