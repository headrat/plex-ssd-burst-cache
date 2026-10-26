import json
import os
import time
from pathlib import Path
s = json.loads(Path('/state/status.json').read_text())
assert s.get('phase') == 'active' and time.time() - s['checked_at'] < 15
assert os.path.ismount('/export/media') and not s.get('fatal_error')
