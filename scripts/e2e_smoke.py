#!/usr/bin/env python3
"""
End-to-end browser test of the workspace against a running app with the SYNTHETIC demo data mounted.

  pip install playwright && python -m playwright install chromium   # once
  python scripts/e2e_smoke.py --url http://localhost:8000 [--shots ./shots] [--chromium /path/to/chrome]

Flow: read data -> load the NQ template -> answer the open questions one at a time -> preview trades on the chart
-> approve & run -> results -> click a trade -> detail shows the approved rules with a verified rules ID.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--shots", type=Path)
    ap.add_argument("--chromium")
    a = ap.parse_args()
    errors: list[str] = []

    def shot(page, name):
        if a.shots:
            a.shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(a.shots / f"{name}.png"))

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chromium) if a.chromium else p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
        pg.goto(a.url)

        # 1. Data
        read = pg.get_by_role("button", name="Read data")
        if read.count():
            read.first.click()
            expect(pg.get_by_text("Finished")).to_be_visible(timeout=300_000)
            pg.keyboard.press("Escape")
            pg.mouse.click(10, 300)
        shot(pg, "1-data")

        # 2. Template (prepared interpretation) -> one question at a time
        pg.get_by_role("button", name="Chat").click()
        pg.get_by_role("button", name="NQ: 10/20/30/60 EMA").click()
        expect(pg.get_by_text("Question 1 of")).to_be_visible(timeout=60_000)
        rules = pg.get_by_text("Lower of EMA60")
        expect(rules).to_be_visible()
        shot(pg, "2-question")
        for _ in range(20):
            if pg.get_by_text("All rules are decided").count():
                break
            opts = pg.locator("div.rounded-md.border-accent\\/50 button.block")
            assert opts.count(), "a question without options appeared"
            opts.first.click()
            pg.wait_for_timeout(600)
        expect(pg.get_by_text("All rules are decided")).to_be_visible()

        # 3. Preview trades on the chart
        expect(pg.get_by_text("Preview of the rules")).to_be_visible(timeout=60_000)
        expect(pg.get_by_text("Preview only — not a backtest result")).to_be_visible()
        shot(pg, "3-preview")

        # 4. Approve & run
        pg.get_by_role("button", name="Approve & run").first.click()
        expect(pg.get_by_text("The backtest will run exactly these rules")).to_be_visible()
        pg.locator("div.fixed button.btn-go").click()
        expect(pg.get_by_text("Net profit")).to_be_visible(timeout=300_000)
        expect(pg.get_by_text("SYNTHETIC DATA")).to_be_visible()
        shot(pg, "4-results")

        # 5. Trade detail
        pg.get_by_role("button", name="List of trades").click()
        pg.locator("tbody tr").first.click()
        expect(pg.get_by_text("Made by the approved rules")).to_be_visible(timeout=60_000)
        assert pg.get_by_text("MISMATCH").count() == 0
        pg.get_by_role("button", name="show rules").click()
        expect(pg.get_by_text("Stop loss").first).to_be_visible()
        shot(pg, "5-trade")
        b.close()

    if errors:
        print("Browser errors:\n" + "\n".join(errors))
        return 1
    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
