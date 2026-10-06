"""Check a folder of audit files and write a report; optionally alert on new problems. See tallyhound/headless.py.

    python scripts/watch.py FOLDER [--alert]

Exit codes: 0 everything was checked, 1 an alert could not be sent (it is retried next run), 3 some data was not
checked (see "Data that was NOT checked" in the report). A missing folder also exits 1.
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
report, new = headless.write_report(folder, res, remember=False)
held = int((res["gate"].decision == "HOLD").sum()) if not res["gate"].empty else 0
problems = headless.data_problems(res)
print(f"{len(res['hits'])} findings ({len(new)} new); {held} payment line(s) on HOLD. Report: {report}")
for n in problems:
    print(f"NOT CHECKED: {n}")
failed: list[str] = []
if a.alert and (new or held or problems):
    sent = headless.send_alert(headless.alert_text(new, held, report, problems), failed)
    print("Alert sent via " + ", ".join(sent) if sent else
          ("" if failed else "No alert channel configured (see tallyhound/headless.py)."))
    for e in failed:
        print(f"ALERT FAILED: {e}")
if not failed:
    headless.remember_hits(folder, res["hits"])      # only once the alert went out; otherwise the next run tries again
# exit codes for schedulers: 0 all checked, 1 an alert could not be sent, 3 some data was not checked
sys.exit(1 if failed else 3 if problems else 0)
