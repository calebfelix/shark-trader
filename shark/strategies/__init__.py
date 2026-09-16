from .mean_reversion import MeanReversionStrategy
from .psar_trend import PSARTrendStrategy
from .regime_switch import RegimeSwitchStrategy
from .scalp import ScalpStrategy
from .sma import SmaCrossoverStrategy

STRATEGIES = {
    "mean_reversion": MeanReversionStrategy,
    "sma": SmaCrossoverStrategy,
    "scalp": ScalpStrategy,
    "psar_trend": PSARTrendStrategy,
    "regime_switch": RegimeSwitchStrategy,
}
