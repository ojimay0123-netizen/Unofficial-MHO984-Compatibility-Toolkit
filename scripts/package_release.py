"""Build a reproducible Windows source ZIP from tracked release files."""
from pathlib import Path
import hashlib
import subprocess
import zipfile

root = Path(__file__).resolve().parents[1]
version = (root / 'VERSION.txt').read_text().strip()
name = f'Unofficial_MHO984_Compatibility_Toolkit_v{version}'
out = root / 'dist'
out.mkdir(exist_ok=True)
archive = out / f'{name}.zip'
files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for relative in sorted(filter(None, files)):
        info = zipfile.ZipInfo(f'{name}/{relative}', date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        z.writestr(info, (root / relative).read_bytes())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(archive.with_suffix('.zip.sha256')).write_text(f'{digest}  {archive.name}\n', encoding='ascii')
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert z.read(f'{name}/VERSION.txt').decode().strip() == version
print(f'{archive.name}: {digest}')
