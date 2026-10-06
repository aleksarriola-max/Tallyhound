"""Create or update a Tallyhound user. Turns sign-in on.

    python scripts/add_user.py alice reviewer
    python scripts/add_user.py bob preparer

Roles: preparer, reviewer, admin. You are asked for the password; it is stored only as a salted hash.
Delete .tallyhound_state/users.json to turn sign-in off again.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tallyhound import auth  # noqa: E402

if len(sys.argv) != 3:
    sys.exit(__doc__)
name, role = sys.argv[1], sys.argv[2]
pw = getpass.getpass(f"Password for {name}: ")
if len(pw) < 8 or pw != getpass.getpass("Again: "):
    sys.exit("Passwords must match and be at least 8 characters.")
auth.add_user(name, role, pw)
print(f"Saved {name} ({role}) to {auth.users_file()}. Sign-in is now on.")
