from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import date

@dataclass
class StockData:
    asset_type: str  # 'KR', 'US', 'Coin'
    ticker: str
    name: str
    price: float
    change_rate: float
    volume: float
    market_cap: float
    actual_date: date
    
    # Optional / Derived fields
    formatted_price: str = ""
    vol_spike: float = 0.0
    theme: str = "-"
    info_link: str = ""
    news: str = "-"
    recommendation: str = "-"
    expert_opinion: str = "-"
    alert_price: str = "-"
    category: str = "" # 'Major', 'Watchlist', etc.

    def to_dict(self) -> Dict:
        return {
            'Asset': self.asset_type,
            'Ticker': self.ticker,
            'Name': self.name,
            'Price': self.price,
            'FormattedPrice': self.formatted_price,
            'ChangeRate': self.change_rate,
            'Volume': self.volume,
            'VolSpike': self.vol_spike,
            'MarketCap': self.market_cap,
            'Theme': self.theme,
            'InfoLink': self.info_link,
            'News': self.news,
            'ActualDate': self.actual_date.isoformat() if self.actual_date else "",
            'Recommendation': self.recommendation,
            'ExpertOpinion': self.expert_opinion,
            'Category': self.category
        }

@dataclass
class MarketIndices:
    name: str
    category: str # 'Index', 'Exchange'
    price: float
    change: float
    rate: float
    date: str

@dataclass
class AppConfig:
    spreadsheet_id: str
    calendar_id: str
    debug_mode: bool = False
    target_date: Optional[date] = None
