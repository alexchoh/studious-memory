#!/usr/bin/env python3
"""Injects data.json into template.html to produce the final dashboard.html
that gets published as the shareable Artifact."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")
TEMPLATE_PATH = os.path.join(HERE, "template.html")
OUT_PATH = os.path.join(HERE, "dashboard.html")


def main():
    with open(DATA_PATH) as f:
        data = json.load(f)

    raw_json = json.dumps(data)
    # Prevent a premature </script> close if any team/player name ever contained it.
    safe_json = raw_json.replace("</script", "<\\/script")

    with open(TEMPLATE_PATH) as f:
        tpl = f.read()

    title = data["league_name"]
    out = tpl.replace("__FPL_DATA_JSON__", safe_json).replace("__PAGE_TITLE__", title)

    with open(OUT_PATH, "w") as f:
        f.write(out)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
