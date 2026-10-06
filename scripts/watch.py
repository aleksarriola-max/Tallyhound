"""Check a folder of audit files and write a report; optionally alert on new problems. See tallyhound/headless.py.

    python scripts/watch.py FOLDER [--alert]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logging  # noqa: E402

logging.getLogger("streamlit").setLevel(logging.ERROR)
from tallyhound import headless  # noqa: E402

p = argparse.ArgumentParser(description="Tallyhound folder check")
p.add_argument("folder")
p.add_argument("--alert", action="store_true", help="send Slack/email alerts for new problems (see environment variables)")
a = p.parse_args()
folder = Path(a.folder)
if not folder.is_dir():
    sys.exit(f"Not a folder: {folder}")
res = headless.run(folder)
report, new = headless.write_report(folder, res)
held = int((res["gate"].decision == "HOLD").sum()) if not res["gate"].empty else 0
print(f"{len(res['hits'])} findings ({len(new)} new); {held} payment line(s) on HOLD. Report: {report}")
if a.alert and (new or held):
    sent = headless.send_alert(headless.alert_text(new, held, report))
    print("Alert sent via " + ", ".join(sent) if sent else "No alert channel configured (see tallyhound/headless.py).")
