from .macd_multi import MacdMultiStrategy
from .mean_reversion import MeanReversionStrategy
from .mean_reversion_v2 import MeanReversionV2Strategy
from .psar_trend import PSARTrendStrategy
from .scalp import ScalpStrategy
from .supply_demand import SupplyDemandStrategy
from .sma import SmaCrossoverStrategy

STRATEGIES = {
    "mean_reversion": MeanReversionStrategy,
    "mean_reversion_v2": MeanReversionV2Strategy,
    "sma": SmaCrossoverStrategy,
    "scalp": ScalpStrategy,
    "psar_trend": PSARTrendStrategy,
    "supply_demand": SupplyDemandStrategy,
    "macd_multi": MacdMultiStrategy,
}
