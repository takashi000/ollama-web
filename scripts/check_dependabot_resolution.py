"""Read-only final gate: no open alerts and every baseline alert fixed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def main() -> None:
    baseline = Path(__file__).resolve().parents[1] / "docs/dependabot-alerts-open-2026-10-25.json"
    pages = json.loads(baseline.read_text(encoding="utf-8-sig"))
    original = [alert for page in pages for alert in page]
    result = subprocess.run(
        [
            "gh", "api", "--paginate",
            "repos/takashi000/ollama-web/dependabot/alerts?per_page=100", "--slurp",
        ],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    alerts = [alert for page in json.loads(result.stdout) for alert in page]
    by_number = {alert["number"]: alert for alert in alerts}
    remaining = [alert["number"] for alert in alerts if alert["state"] == "open"]
    not_fixed = {
        alert["number"]: by_number.get(alert["number"], {}).get("state", "missing")
        for alert in original
        if by_number.get(alert["number"], {}).get("state") != "fixed"
    }
    print(json.dumps({"open": remaining, "baseline_not_fixed": not_fixed}, indent=2))
    if remaining or not_fixed:
        raise SystemExit("Dependabot completion gate has not passed")
    print(f"PASS: zero open alerts; all {len(original)} baseline alerts fixed")


if __name__ == "__main__":
    main()
