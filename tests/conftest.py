import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import tempfile

# keep test runs from writing into the project folder
os.environ["TALLYHOUND_STATE_DIR"] = tempfile.mkdtemp(prefix="tallyhound-test-")
try:                                       # ... and hypothesis from keeping its example cache there
    from hypothesis.configuration import set_hypothesis_home_dir
    set_hypothesis_home_dir(tempfile.mkdtemp(prefix="tallyhound-hypothesis-"))
except ImportError:
    pass
