"""Exit 0 if the freshly built data files match the committed ones (ignoring the "updated" date)."""
import json, subprocess, sys

for name in ("combined.json", "offense.json", "idp.json"):
    try:
        old = json.loads(subprocess.run(["git", "show", f"HEAD:data/{name}"], capture_output=True, check=True).stdout)
    except subprocess.CalledProcessError:
        sys.exit(1)  # file not committed yet
    new = json.load(open(f"data/{name}"))
    old.pop("updated", None)
    new.pop("updated", None)
    if old != new:
        sys.exit(1)
sys.exit(0)
