"""Contract specifications and symbol parsing for the supported futures."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    root: str
    name: str
    tick_size: float
    tick_value: float  # USD per tick per contract
    exchange: str
    calendar: str  # pandas_market_calendars name used for gap checks
    rth_start: str  # HH:MM America/New_York
    rth_end: str
    rth_note: str

    @property
    def point_value(self) -> float:
        return self.tick_value / self.tick_size

    @property
    def price_decimals(self) -> int:
        s = f"{self.tick_size:.10f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0


CONTRACTS: dict[str, ContractSpec] = {
    "NQ": ContractSpec("NQ", "E-mini Nasdaq-100", 0.25, 5.0, "CME", "CME_Equity", "09:30", "16:00",
                       "US cash equity hours 09:30-16:00 ET"),
    "MNQ": ContractSpec("MNQ", "Micro E-mini Nasdaq-100", 0.25, 0.5, "CME", "CME_Equity", "09:30", "16:00",
                        "US cash equity hours 09:30-16:00 ET"),
    "GC": ContractSpec("GC", "COMEX Gold", 0.10, 10.0, "COMEX", "CMEGlobex_Gold", "08:20", "13:30",
                       "COMEX gold day session 08:20-13:30 ET"),
    "MGC": ContractSpec("MGC", "Micro Gold", 0.10, 1.0, "COMEX", "CMEGlobex_Gold", "08:20", "13:30",
                        "COMEX gold day session 08:20-13:30 ET"),
}

# CME Globex electronic session for both products: 18:00 ET -> 17:00 ET next day.
ETH_START = "18:00"
ETH_END = "17:00"

MONTH_CODES = "FGHJKMNQUVXZ"
_OUTRIGHT = re.compile(r"^(MNQ|NQ|MGC|GC)([FGHJKMNQUVXZ])(\d{1,2})$")


@dataclass(frozen=True)
class ParsedSymbol:
    raw: str
    root: str | None
    kind: str  # "outright" | "spread" | "unknown"
    month: str | None = None
    year: str | None = None


def parse_symbol(raw: str | None) -> ParsedSymbol:
    if not raw:
        return ParsedSymbol(raw or "", None, "unknown")
    s = raw.strip()
    if "-" in s or ":" in s or " " in s:
        m = _OUTRIGHT.match(s.split("-")[0].split(":")[0].strip())
        return ParsedSymbol(s, m.group(1) if m else None, "spread")
    m = _OUTRIGHT.match(s)
    if m:
        return ParsedSymbol(s, m.group(1), "outright", m.group(2), m.group(3))
    return ParsedSymbol(s, None, "unknown")


def round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 10)
