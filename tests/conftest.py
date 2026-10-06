import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import tempfile

# keep test runs from writing into the project folder
os.environ["TALLYHOUND_STATE_DIR"] = tempfile.mkdtemp(prefix="tallyhound-test-")
