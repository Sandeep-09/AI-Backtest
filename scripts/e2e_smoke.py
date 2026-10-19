#!/usr/bin/env python3
"""
End-to-end browser test of the whole workflow against a running app that has the
SYNTHETIC demo data mounted (see README "Try it with synthetic data").

  pip install playwright && python -m playwright install chromium   # once
  python scripts/e2e_smoke.py --url http://localhost:8000 [--shots ./shots] [--chromium /path/to/chrome]

Steps: read data -> describe "10 EMA and 20 EMA with 1:3" -> confirm no crossover was assumed ->
answer open questions -> add entry trigger in chat -> preview from data -> modify target in controls
(new version, highlighted) -> approve & run -> open a trade -> rules + hash verified.
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
    ap.add_argument("--chromium", help="Path to a Chromium executable (optional)")
    a = ap.parse_args()
    errors: list[str] = []

    def shot(page, name):
        if a.shots:
            a.shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(a.shots / f"{name}.png"), full_page=True)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chromium) if a.chromium else p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)

        # 1. Data
        pg.goto(f"{a.url}/#/data")
        read = pg.get_by_role("button", name="Read")
        expect(read).to_be_visible()
        if read.is_enabled():
            read.click()
        expect(pg.get_by_text("NQ coverage")).to_be_visible(timeout=180_000)
        expect(pg.get_by_text("SYNTHETIC test data").first).to_be_visible()
        shot(pg, "1-data")

        # 2. Describe
        pg.get_by_role("button", name="2 · Describe").click()
        pg.fill("textarea", "Use the 10 EMA and 20 EMA with a 1:3 risk-to-reward target.")
        pg.get_by_role("button", name="Interpret strategy").click()
        expect(pg.get_by_text("Rules still to decide")).to_be_visible(timeout=60_000)
        rules = pg.locator("section:has-text('The rules that will be tested')")
        expect(rules.get_by_text("No relationship between them is assumed")).to_be_visible()
        assert "crosses" not in rules.inner_text(), "a crossover was assumed from naming the indicators"
        expect(pg.get_by_text("What exactly triggers an entry?")).to_be_visible()
        expect(pg.get_by_role("button", name="Approve strategy and run backtest")).to_have_count(0)
        shot(pg, "2-describe")

        # 3. Answer open questions explicitly (first option each), then add the trigger in chat.
        for _ in range(30):
            btns = pg.locator("section:has-text('Rules still to decide') li button")
            if btns.count() == 0:
                break
            btns.first.click()
            pg.wait_for_timeout(500)
        pg.get_by_role("button", name="Modify strategy").click()
        pg.fill("input[placeholder^='e.g. Only take longs']",
                "Go long when the close crosses above the 10 EMA. Go short when the close crosses below the 10 EMA.")
        pg.get_by_role("button", name="Send").click()
        pg.wait_for_timeout(1200)
        dates = pg.locator("button:has-text('All available data')")
        if dates.count():
            dates.first.click()
        expect(pg.get_by_text("These examples are produced by the same engine")).to_be_visible(timeout=60_000)
        expect(pg.get_by_text("First long trade in your data")).to_be_visible()
        expect(pg.get_by_text("1 · Signal")).to_be_visible()
        shot(pg, "3-preview")

        # 4. Modify via controls -> new version, changed section highlighted, approval reset.
        pg.get_by_role("button", name="Edit controls").click()
        pg.locator("fieldset:has-text('Profit target') input[type=number]").first.fill("2")
        pg.get_by_role("button", name="Apply 1 change (new version)").click()
        expect(pg.get_by_text("changed in v").first).to_be_visible(timeout=30_000)
        expect(pg.get_by_text("These examples are produced by the same engine")).to_be_visible(timeout=60_000)
        shot(pg, "4-modified")

        # 5. Approve & run
        pg.get_by_role("button", name="Review assumptions & approve").click()
        expect(pg.get_by_text("Execution limitations")).to_be_visible()
        go = pg.get_by_role("button", name="Approve strategy and run backtest")
        expect(go).to_be_enabled(timeout=30_000)
        shot(pg, "5-approve")
        go.click()
        expect(pg.get_by_text("Equity curve")).to_be_visible(timeout=300_000)

        # 6. Inspect a trade
        pg.locator("tbody tr").first.click()
        expect(pg.get_by_text("hash verified")).to_be_visible(timeout=60_000)
        expect(pg.get_by_text("Approved rules that produced this trade")).to_be_visible()
        expect(pg.get_by_text("6 · Costs & result")).to_be_visible()
        pg.get_by_placeholder("search: date, stop, NQZ6…").fill("short")
        pg.wait_for_timeout(800)
        dirs = pg.locator("tbody tr td:nth-child(2)").all_inner_texts()
        assert dirs and all(d == "short" for d in dirs), dirs
        shot(pg, "6-results")

        # 7. Import an interpretation written outside the app (Claude in chat); every value must pass the quote check.
        interp = Path(__file__).resolve().parents[1] / "strategies" / "ema_vwap_strong_candle.json"
        if interp.exists():
            pg.get_by_role("button", name="New strategy").click()
            pg.set_input_files("input[type=file]", str(interp))
            expect(pg.get_by_text("Rules still to decide")).to_be_visible(timeout=60_000)
            expect(pg.get_by_text("imported")).to_be_visible()
            assert pg.get_by_text("needs confirmation").count() == 0, "an imported value lacked a quote"
            expect(pg.get_by_text("1.5 × ATR(14) of the previous bar")).to_be_visible()
            shot(pg, "7-imported")
        b.close()

    if errors:
        print("Browser errors:\n" + "\n".join(errors))
        return 1
    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
