import zipfile
import io
import base64
from pathlib import Path

buf = io.BytesIO()
files = [
    'main.py',
    'src/pipeline.py',
    'src/telegram_client.py',
    'src/parallel_uploader.py',
    'src/database.py',
    'src/gdrive.py',
]
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    for f in files:
        z.write(f, arcname=f)

b64_str = base64.b64encode(buf.getvalue()).decode('ascii')

script_content = f"""python3 -c "import base64, zipfile, io; raw = base64.b64decode('{b64_str}'); zipfile.ZipFile(io.BytesIO(raw)).extractall('.')" && nohup .venv/bin/python main.py --gdrive --delete-after-upload --reverse --max-gb 90 --workers 4 > vps_migration.log 2>&1 &
sleep 1
tail -n 15 vps_migration.log
"""

out_file = Path('data/vps_patch_command.txt')
out_file.write_text(script_content)
print(f"Generated patch script in {out_file} ({len(script_content)} chars)")
