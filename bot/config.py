"""
config.py - All the settings you can change, in one place.

Beginner tip: you can edit the numbers here without touching the other files.
"""

# ---------------------------------------------------------------------------
# 1. Which coins to watch
#    "name" is what you see in Telegram.
#    "keywords" are used to find news headlines about that coin.
# ---------------------------------------------------------------------------
COINS = [
    {"symbol": "BTCUSDT", "name": "BTC", "keywords": ["bitcoin", "btc"]},
    {"symbol": "ETHUSDT", "name": "ETH", "keywords": ["ethereum", "eth", "ether"]},
    {"symbol": "SOLUSDT", "name": "SOL", "keywords": ["solana", "sol"]},
    {"symbol": "XRPUSDT", "name": "XRP", "keywords": ["xrp", "ripple"]},
    {"symbol": "BNBUSDT", "name": "BNB", "keywords": ["bnb", "binance coin"]},
    {"symbol": "DOGEUSDT", "name": "DOGE", "keywords": ["dogecoin", "doge"]},
    {"symbol": "ADAUSDT", "name": "ADA", "keywords": ["cardano"]},
    {"symbol": "AVAXUSDT", "name": "AVAX", "keywords": ["avalanche", "avax"]},
    {"symbol": "LINKUSDT", "name": "LINK", "keywords": ["chainlink"]},
]

# Extra coins that news alerts may suggest. They are NOT checked every 10 minutes; a button under the
# alert gives a full look at the coin on demand (Binance must have a SYMBOLUSDT pair for it).
EXTRA_COINS = [
    {"symbol": "TRXUSDT", "name": "TRX", "keywords": ["tron", "trx"]},
    {"symbol": "TONUSDT", "name": "TON", "keywords": ["toncoin", "the open network"]},
    {"symbol": "DOTUSDT", "name": "DOT", "keywords": ["polkadot"]},
    {"symbol": "LTCUSDT", "name": "LTC", "keywords": ["litecoin", "ltc"]},
    {"symbol": "BCHUSDT", "name": "BCH", "keywords": ["bitcoin cash"]},
    {"symbol": "NEARUSDT", "name": "NEAR", "keywords": ["near protocol"]},
    {"symbol": "APTUSDT", "name": "APT", "keywords": ["aptos"]},
    {"symbol": "ARBUSDT", "name": "ARB", "keywords": ["arbitrum"]},
    {"symbol": "SUIUSDT", "name": "SUI", "keywords": ["sui network", "sui token"]},
    {"symbol": "INJUSDT", "name": "INJ", "keywords": ["injective"]},
    {"symbol": "ATOMUSDT", "name": "ATOM", "keywords": ["cosmos hub", "cosmos atom"]},
    {"symbol": "UNIUSDT", "name": "UNI", "keywords": ["uniswap"]},
    {"symbol": "AAVEUSDT", "name": "AAVE", "keywords": ["aave"]},
    {"symbol": "FILUSDT", "name": "FIL", "keywords": ["filecoin"]},
    {"symbol": "PEPEUSDT", "name": "PEPE", "keywords": ["pepe"]},
    {"symbol": "SHIBUSDT", "name": "SHIB", "keywords": ["shiba inu", "shib"]},
    {"symbol": "WLDUSDT", "name": "WLD", "keywords": ["worldcoin"]},
    {"symbol": "HBARUSDT", "name": "HBAR", "keywords": ["hedera", "hbar"]},
    {"symbol": "ICPUSDT", "name": "ICP", "keywords": ["internet computer"]},
    {"symbol": "ETCUSDT", "name": "ETC", "keywords": ["ethereum classic"]},
    {"symbol": "XLMUSDT", "name": "XLM", "keywords": ["stellar lumens", "xlm"]},
    {"symbol": "POLUSDT", "name": "POL", "keywords": ["polygon"]},
]
ALL_COINS = COINS + EXTRA_COINS   # every coin a news alert or button can name

# ---------------------------------------------------------------------------
# 2. Chart settings
# ---------------------------------------------------------------------------
TIMEFRAME = "15m"     # candle size the bot watches and alerts on (day trading: 15m entries)
TIMEFRAMES = ["15m", "30m", "1h"]  # sizes on the coin buttons under messages (Binance has no 10m candle)
HTF_FOR = {"15m": "1h", "30m": "1h", "1h": "4h"}  # bigger candle used to confirm the trend
CANDLES = 200         # how many past candles to download (enough for EMA50)

# ---------------------------------------------------------------------------
# 3. Indicator settings
# ---------------------------------------------------------------------------
EMA_FAST = 20         # fast moving average
EMA_SLOW = 50         # slow moving average
RSI_PERIOD = 14
ATR_PERIOD = 14       # ATR = average candle size, used for stop loss / target

# RSI levels used by the strategy (see signals.py for the full rules)
RSI_PULLBACK_LONG = 40    # in an up-trend, RSI crossing UP through 40 = LONG
RSI_PULLBACK_SHORT = 60   # in a down-trend, RSI crossing DOWN through 60 = SHORT
EXTRA_SETUPS = True       # True = also signal when a trend resumes (more signals per day)
RSI_MOMENTUM = 50         # in a trend, RSI crossing through 50 = momentum is back
RSI_OVERBOUGHT = 70       # don't go LONG above this
RSI_OVERSOLD = 30         # don't go SHORT below this

# Filters that cut false signals (the first results showed mostly sideways chop)
ADX_PERIOD = 14
ADX_MIN = 25          # ADX below this = no real trend, so no signal (backtest: 25 beat 20)
HTF_FILTER = True     # True = don't go against the trend of the bigger candle (see HTF_FOR)
HTF_STRICT = True     # True = only trade WITH the bigger candle's trend (a sideways 1h blocks too)
FLIP_COOLDOWN_CANDLES = 3  # after LONG, no SHORT for this many candles (and the other way round)

# Fewer, better signals. A LONG/SHORT is only sent when the trend, the news and Claude's review all
# agree (Claude CAUTION / AVOID = no signal). Coins move together: 6 SHORTs on the same candle are
# really one trade, so only the strongest (highest ADX) is sent and the others with the same direction
# are listed in it as "also signaling". Every signal not sent is logged quietly in history.json, so
# /stats can show whether the filters help (see GLOSSARY.md).
MAX_SIGNALS_PER_RUN = 2      # one LONG group + one SHORT group can both go out
MAX_SIGNALS_PER_DAY = 8      # per local day (LOCAL_UTC_OFFSET_HOURS)
SEND_CONFIDENCE = ["HIGH", "MEDIUM"]  # HIGH = news agrees too; MEDIUM = news neutral (most news is). ["HIGH"] = fewer

# ---------------------------------------------------------------------------
# 4. Risk settings (shown in the alert, the bot does NOT trade)
# ---------------------------------------------------------------------------
# Stop loss goes just beyond the recent swing low (LONG) / swing high (SHORT),
# but never closer than MIN_STOP_ATR or farther than MAX_STOP_ATR (in ATRs).
SWING_LOOKBACK = 10   # how many closed candles to look back for the swing point
SWING_BUFFER_ATR = 0.25  # extra room beyond the swing point (in ATRs)
MIN_STOP_ATR = 1.0
MAX_STOP_ATR = 2.0
STOP_ATR = 1.5        # stop distance used when there is no swing data (fallback)
ENTRY_ZONE_ATR = 0.25  # entry zone: wait up to this far (in ATRs) for a slightly better price
REWARD_RISK = 1.5     # take profit = stop distance x 1.5 (backtest: hit far more often than 2:1)
LEVERAGE_RANGES = [(10, 20), (20, 40), (40, 60), (60, 100)]  # one plan per range (see leverage.py)
LEVERAGE_BUTTONS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]  # buttons under coin messages: tap = plan at exactly that leverage
MAINTENANCE_MARGIN = 0.005  # rough exchange maintenance margin (0.5%), used for the liquidation price
STOP_SAFETY = 0.6     # stop is kept within 60% of the distance to liquidation
LEVERAGE = 2          # leverage you plan to use (only for the warning text)

# ---------------------------------------------------------------------------
# 5. News settings
# ---------------------------------------------------------------------------
NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]
MAX_HEADLINES = 12    # max headlines sent to Claude (fewer = less Pro usage)

# ---------------------------------------------------------------------------
# 6. Claude settings
# ---------------------------------------------------------------------------
USE_CLAUDE = True           # False = always use the free VADER backup
CLAUDE_MODEL = "haiku"      # small, fast model is enough for headlines
CLAUDE_TIMEOUT_SEC = 120    # give up after 2 minutes and use the backup

# ---------------------------------------------------------------------------
# 7. Daily summary
#    Hour in UTC. 1 = 08:00 in UTC+7 time.
#    Set to None to turn the daily summary off.
# ---------------------------------------------------------------------------
DAILY_SUMMARY_HOUR_UTC = 1

# Time zone for the "Analysed" time shown on alerts (hours from UTC).
LOCAL_UTC_OFFSET_HOURS = 7

# ---------------------------------------------------------------------------
# 8. Event alerts (listings, ETF approvals, hacks, delistings, ...)
#    Free, rule based, scans any coin in the news. See events.py for the rules.
# ---------------------------------------------------------------------------
EVENT_ALERTS = True          # False = turn event alerts off
MAX_EVENTS_PER_RUN = 5       # max event alerts sent in one 10-minute run
EVENTS_WATCHED_ONLY = False  # True = only events that mention BTC/ETH/SOL
EVENT_CLAUDE_CONFIRM = True  # True = Claude checks the found events (one call per run) before they are sent

# ---------------------------------------------------------------------------
# 9. Macro alerts (Fed, inflation, tariffs, war, bank trouble, market crashes)
#    Big world news that can move oil, gold, stocks AND crypto.
#    A keyword filter picks candidate headlines, then Claude judges them in ONE
#    call per run. Needs Claude (USE_CLAUDE = True); without it macro alerts are off.
# ---------------------------------------------------------------------------
MACRO_ALERTS = True
MACRO_FEEDS = [
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "http://feeds.bbci.co.uk/news/business/rss.xml",
]
MACRO_MAX_AGE_HOURS = 6      # ignore older headlines
MACRO_MAX_CANDIDATES = 10    # max headlines sent to Claude per run
MACRO_MAX_ALERTS = 3         # max macro alerts sent per run
MACRO_IDEA_COINS = ["BTC", "ETH", "SOL"]  # used when Claude names no coin for a macro headline
MACRO_CONFLICT_HOURS = 4     # a macro alert against a signal sent this recently is not sent; a note is
                             # added as a reply to that signal instead

# ---------------------------------------------------------------------------
# 10. Result tracking (win rate, the /stats command)
#     Every signal is judged later: take profit first = WIN, stop loss first = LOSS.
# ---------------------------------------------------------------------------
TRACK_RESULTS = True         # False = no result messages and no /stats
RESULT_EXPIRE_HOURS = 6      # 1h candles: a signal that hits neither level in this time = EXPIRED
EXPIRE_HOURS_BY_TF = {"15m": 4, "30m": 3}  # 15m = 4h day trade, 30m = shorter wait (1h uses the line above)
CHECK_EVERY_MIN = 10         # laptop mode (`python -m bot watch`): minutes between price checks
LOOP_EVERY_MIN = 5           # `python -m bot loop` (crypto + gold/forex): minutes between checks
                             # `loop --for-min=330` stops after 330 min (GitHub Actions jobs end at 6h)
MIN_SAMPLE = 30              # /stats warns until this many signals have finished

# ---------------------------------------------------------------------------
# 11. Files used by the bot (you don't need to change these)
# ---------------------------------------------------------------------------
STATE_FILE = "state.json"            # remembers alerts already sent
PENDING_FILE = "pending_signals.json"  # hand-off between the two steps
HISTORY_FILE = "history.json"        # every signal and its result

# ---------------------------------------------------------------------------
# 12. Gold and forex (XAU/USD, EUR/USD, GBP/USD, USD/JPY) - day trading
#     Runs with `python -m bot run`, `fx` and `loop` on your laptop, not on GitHub Actions.
#     Candles: Twelve Data (free key in TWELVEDATA_API_KEY), Yahoo Finance as backup.
#     Strategy: fx_signals.py. Filters and limits: fx.py (same rules as crypto).
# ---------------------------------------------------------------------------
FX_ENABLED = True
# digits = decimals shown. currencies = big news in these currencies holds the signal.
# *_words = news keywords (also used by the VADER backup: first currency vs second currency).
FX_PAIRS = [
    {"symbol": "XAU/USD", "name": "XAU/USD", "yahoo": "GC=F", "digits": 2, "currencies": ["USD"],
     "base_words": ["gold", "bullion"], "quote_words": ["dollar", "greenback"]},
    {"symbol": "EUR/USD", "name": "EUR/USD", "yahoo": "EURUSD=X", "digits": 5, "currencies": ["EUR", "USD"],
     "base_words": ["euro", "ecb", "eurozone"], "quote_words": ["dollar", "greenback"]},
    {"symbol": "GBP/USD", "name": "GBP/USD", "yahoo": "GBPUSD=X", "digits": 5, "currencies": ["GBP", "USD"],
     "base_words": ["pound", "sterling", "bank of england", "boe"], "quote_words": ["dollar", "greenback"]},
    {"symbol": "USD/JPY", "name": "USD/JPY", "yahoo": "USDJPY=X", "digits": 3, "currencies": ["USD", "JPY"],
     "base_words": ["dollar", "greenback"], "quote_words": ["yen", "boj", "bank of japan"]},
]
FX_CANDLES = {"15m": 5000, "1d": 400}       # candles downloaded (1h trend candles are built from the 15m ones)
FX_MIN_CANDLES = {"15m": 1000, "1d": 220}   # fewer = the download counts as failed

# Strategy: entries on 15m candles, only in the direction of the 1h trend
FX_TIMEFRAME = "15m"
FX_TREND_TF = "1h"          # trend: price and EMA50 above EMA200 = LONG only, below = SHORT only
FX_TREND_FALLBACK_TF = "15m"  # 1h trend sideways = follow this trend instead (more signals); None = WAIT
FX_EMA_ENTRY = 20           # pullback line
FX_EMA_TREND_FAST = 50
FX_EMA_TREND_SLOW = 200
FX_BREAKOUT_CANDLES = 12    # breakout = close beyond the high / low of this many candles (12 = last 3h)
FX_RSI_LONG = (45, 75)      # RSI must be inside this range for a LONG
FX_RSI_SHORT = (25, 55)     # ... and this one for a SHORT
FX_MAX_ATR_FROM_EMA = 2.0   # skip when price is more than this many ATRs from EMA20 (too late)
FX_STOP_ATR = 1.5           # stop loss = 1.5 x the 15m ATR
FX_REWARD_RISK = 1.5        # take profit = 1.5R, reachable within a few hours
FX_EXPIRE_HOURS = 4         # neither TP nor stop within 4 hours = EXPIRED (day trading, never overnight)

# Fewer, higher-quality signals (same rules as crypto: trend, news and Claude must all agree)
FX_MAX_SIGNALS_PER_RUN = 1
FX_MAX_SIGNALS_PER_DAY = 6      # gold / forex together, per local day (LOCAL_UTC_OFFSET_HOURS)
FX_SEND_CONFIDENCE = ["HIGH", "MEDIUM"]  # HIGH = news agrees; MEDIUM = news neutral. ["HIGH"] = fewer

# Market hours (UTC): closed from Friday FX_CLOSE_HOUR_UTC to Sunday FX_OPEN_HOUR_UTC
FX_CLOSE_HOUR_UTC = 22
FX_OPEN_HOUR_UTC = 21

# Big news hold: LONG / SHORT becomes WAIT this many minutes before a big event (Fed, CPI, NFP)
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"  # ForexFactory, this week
NEWS_HOLD_MINUTES = 60
NEWS_HOLD_IMPACT = ["High"]      # ForexFactory impact levels that count as big news
CALENDAR_REFRESH_HOURS = 6       # calendar is cached in state.json and downloaded again after this

# News for the Claude check (+ = the pair's price goes UP)
FX_NEWS_FEEDS = MACRO_FEEDS + ["https://www.fxstreet.com/rss/news"]
FX_MAX_HEADLINES = 15

# Daily bias: one message every morning from daily candles (hour in your time, LOCAL_UTC_OFFSET_HOURS)
FX_DAILY_SIGNAL = True
FX_DAILY_HOUR_LOCAL = 6          # 6:00 Phnom Penh time (UTC+7)
