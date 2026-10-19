"""
StrategySpec: the single source of truth for a strategy.

Every field a user must decide is Optional and starts as None. Nothing here has
a hidden default for a strategy decision; completeness.py turns each None
into a question. The preview and the backtest both read this object only.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

Instrument = Literal["NQ", "MNQ", "GC", "MGC"]
Timeframe = Literal["1m", "2m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h"]
PriceField = Literal["open", "high", "low", "close"]
IndicatorType = Literal["ema", "sma", "atr", "rsi", "vwap", "highest", "lowest", "prior_session_high", "prior_session_low"]

TIMEFRAME_MINUTES: dict[str, int] = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
                                     "1h": 60, "2h": 120, "4h": 240}

# Indicator types that need a period, and which accept a price source.
NEEDS_PERIOD = {"ema", "sma", "atr", "rsi", "highest", "lowest"}
TAKES_SOURCE = {"ema", "sma", "rsi", "highest", "lowest"}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Indicator(_M):
    id: str = Field(description="Short unique id used by conditions, e.g. 'ema10'")
    type: IndicatorType
    period: Optional[int] = Field(default=None, ge=1, le=1000)
    source: Optional[Literal["open", "high", "low", "close", "hl2", "hlc3"]] = None


class Operand(_M):
    kind: Literal["indicator", "price", "const"]
    ref: Optional[str] = Field(default=None, description="Indicator id when kind=indicator")
    field: Optional[PriceField] = Field(default=None, description="Bar field when kind=price")
    value: Optional[float] = Field(default=None, description="Number when kind=const")
    bars_ago: int = Field(default=0, ge=0, le=500, description="0 = the signal bar, 1 = the bar before it")
    offset_ticks: int = Field(default=0, ge=-10000, le=10000)


class Compare(_M):
    type: Literal["compare"] = "compare"
    op: Literal["gt", "lt", "gte", "lte", "crosses_above", "crosses_below", "touches"]
    left: Operand
    right: Operand


class Group(_M):
    type: Literal["all", "any"]
    conditions: list["Condition"] = Field(default_factory=list)


Condition = Annotated[Union[Compare, Group], Field(discriminator="type")]
Group.model_rebuild()


class DirectionRules(_M):
    when: Optional[Group] = Field(default=None, description="Signal condition, evaluated on the closed signal bar")
    exit_when: Optional[Group] = Field(default=None, description="Optional rule-based exit, evaluated at bar close")


class EntryOrder(_M):
    type: Literal["market_next_open", "stop_beyond_signal_bar", "limit_at_level"]
    offset_ticks: int = Field(default=0, ge=0, le=1000, description="Ticks beyond the signal bar high/low for stop entries")
    level: Optional[Operand] = Field(default=None, description="Limit price for limit_at_level, read at the signal bar")
    valid_bars: int = Field(default=1, ge=1, le=100, description="Bars the stop/limit order stays working")


class TimeWindow(_M):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class Entry(_M):
    long: Optional[DirectionRules] = None
    short: Optional[DirectionRules] = None
    order: Optional[EntryOrder] = None
    time_window: Optional[TimeWindow] = Field(default=None, description="Signals only within this window (session timezone)")


class Stop(_M):
    type: Literal["fixed_ticks", "atr_multiple", "signal_bar_extreme", "swing_extreme", "indicator"]
    ticks: Optional[int] = Field(default=None, ge=1, le=100000, description="Distance from fill for fixed_ticks")
    atr_ref: Optional[str] = Field(default=None, description="ATR indicator id for atr_multiple")
    multiple: Optional[float] = Field(default=None, gt=0, le=100)
    lookback: Optional[int] = Field(default=None, ge=1, le=500, description="Bars for swing_extreme")
    ref: Optional[str] = Field(default=None, description="Indicator id for indicator stops")
    offset_ticks: int = Field(default=0, ge=0, le=10000, description="Extra ticks beyond the level")


class Target(_M):
    type: Literal["r_multiple", "fixed_ticks", "indicator", "none"]
    r: Optional[float] = Field(default=None, gt=0, le=100, description="Reward as a multiple of initial risk")
    ticks: Optional[int] = Field(default=None, ge=1, le=100000)
    ref: Optional[str] = None


class Trail(_M):
    type: Literal["indicator", "ticks"]
    ref: Optional[str] = None
    ticks: Optional[int] = Field(default=None, ge=1, le=100000)
    activate_at_r: Optional[float] = Field(default=None, ge=0, le=100)


class Management(_M):
    flat_at_session_end: Optional[bool] = None
    breakeven_at_r: Optional[float] = Field(default=None, gt=0, le=100)
    breakeven_offset_ticks: int = Field(default=0, ge=0, le=1000)
    trail: Optional[Trail] = None
    time_exit_bars: Optional[int] = Field(default=None, ge=1, le=10000)
    max_trades_per_day: Optional[int] = Field(default=None, ge=1, le=1000)
    exit_on_opposite_signal: bool = False
    cooldown_bars: int = Field(default=0, ge=0, le=10000)


class Sizing(_M):
    type: Literal["fixed_contracts", "risk_usd"]
    contracts: Optional[int] = Field(default=None, ge=1, le=1000)
    risk_usd: Optional[float] = Field(default=None, gt=0)
    max_contracts: Optional[int] = Field(default=None, ge=1, le=1000)
    below_one: Literal["skip", "one"] = "skip"


class Costs(_M):
    commission_per_side: Optional[float] = Field(default=None, ge=0, description="USD per contract per side, incl. exchange fees")
    slippage_ticks: Optional[float] = Field(default=None, ge=0, description="Ticks lost on market and stop fills")


class Fills(_M):
    same_bar_policy: Optional[Literal["intrabar_then_stop_first", "intrabar_then_target_first", "stop_first", "target_first"]] = None
    limit_fill: Optional[Literal["touch", "trade_through"]] = None


class Session(_M):
    type: Literal["rth", "eth", "custom"]
    start: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    end: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    tz: str = "America/New_York"
    indicators_use: Optional[Literal["session_bars_only", "all_hours"]] = None


class DataSelection(_M):
    contract: Optional[Literal["front_by_prior_volume", "most_active_same_day", "specific"]] = None
    symbol: Optional[str] = None
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None


class StrategySpec(_M):
    schema_version: int = 1
    name: Optional[str] = None
    instrument: Optional[Instrument] = None
    timeframe: Optional[Timeframe] = None
    session: Optional[Session] = None
    display_timezone: Optional[str] = None
    directions: Optional[Literal["long", "short", "both"]] = None
    indicators: list[Indicator] = Field(default_factory=list)
    entry: Entry = Field(default_factory=Entry)
    stop: Optional[Stop] = None
    target: Optional[Target] = None
    management: Management = Field(default_factory=Management)
    sizing: Optional[Sizing] = None
    costs: Costs = Field(default_factory=Costs)
    fills: Fills = Field(default_factory=Fills)
    data: DataSelection = Field(default_factory=DataSelection)

    def indicator(self, ref: str | None) -> Indicator | None:
        for i in self.indicators:
            if i.id == ref:
                return i
        return None

    def enabled_directions(self) -> list[str]:
        if self.directions == "both":
            return ["long", "short"]
        if self.directions in ("long", "short"):
            return [self.directions]
        return []


# Paths the AI (or the editable controls) may set. Anything else is rejected.
SETTABLE_PATHS: dict[str, str] = {
    "name": "Strategy name",
    "instrument": "Instrument",
    "timeframe": "Bar timeframe",
    "session": "Trading session",
    "session.indicators_use": "Which bars feed the indicators",
    "display_timezone": "Display timezone",
    "directions": "Directions traded",
    "indicators": "Indicators (full list)",
    "entry.long": "Long signal rules",
    "entry.short": "Short signal rules",
    "entry.long.when": "Long signal condition",
    "entry.short.when": "Short signal condition",
    "entry.long.exit_when": "Long rule-based exit",
    "entry.short.exit_when": "Short rule-based exit",
    "entry.order": "Entry order type and timing",
    "entry.time_window": "Time-of-day filter for signals",
    "stop": "Stop-loss rule",
    "target": "Profit target rule",
    "management": "Trade management (whole block)",
    "management.flat_at_session_end": "Flat at session end",
    "management.breakeven_at_r": "Move stop to breakeven at R",
    "management.breakeven_offset_ticks": "Breakeven offset ticks",
    "management.trail": "Trailing stop",
    "management.time_exit_bars": "Time exit after N bars",
    "management.max_trades_per_day": "Max trades per day",
    "management.exit_on_opposite_signal": "Exit on opposite signal",
    "management.cooldown_bars": "Cooldown bars after an exit",
    "sizing": "Position sizing",
    "costs.commission_per_side": "Commission per side",
    "costs.slippage_ticks": "Slippage ticks",
    "fills.same_bar_policy": "Same-bar stop/target policy",
    "fills.limit_fill": "Limit/target fill rule",
    "data": "Data selection (whole block)",
    "data.contract": "Contract selection",
    "data.symbol": "Specific contract symbol",
    "data.start": "Backtest start date",
    "data.end": "Backtest end date",
}
