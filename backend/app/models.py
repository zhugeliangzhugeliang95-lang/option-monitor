from __future__ import annotations
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo
from .instruments import FUTU_SYMBOLS
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Basis = Literal['BID','ASK','MID','LAST']
Feed = Literal['REALTIME','DELAYED','INDICATIVE','MANUAL','MOCK','UNKNOWN']
SYMBOL_MAX=20
SYMBOL_RE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,%d}'%(SYMBOL_MAX-1))
def check_symbol(value,field='symbol'):
    """Format check for a NEW or CHANGED symbol/underlyingId (create, or update that changes it).
    Not applied when reading stored configs, so legacy monitors with odd symbols keep working."""
    v=value.strip() if isinstance(value,str) else value
    if not isinstance(v,str) or not v: raise ValueError(f'标的代码不能为空 / {field} must not be empty')
    if not SYMBOL_RE.fullmatch(v):
        raise ValueError(f'标的代码格式无效：只允许字母、数字和 . - _，以字母或数字开头，最长 {SYMBOL_MAX} 个字符 / '
                         f'Invalid {field} {v[:40]!r}: use letters, digits, ".", "-", "_" (start with a letter or digit, max {SYMBOL_MAX} chars)')
    return v
def utcnow(): return datetime.now(timezone.utc)
class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
class Config(Model):
    symbol: str = 'SPY'
    underlyingId: str = 'SPY'
    currency: str = 'USD'
    exchange: str = 'XNYS'
    exchangeTimezone: str = 'America/New_York'
    calendar: str = 'XNYS'
    expiryMode: Literal['sameDay','manual'] = 'sameDay'
    expiration: str | None = None
    provider: Literal['manual','mock','http','futu'] = 'manual'
    dataMode: Literal['MANUAL','MOCK','API'] = 'MANUAL'
    nBelow: int = Field(0, ge=0, le=50)
    nAbove: int = Field(0, ge=0, le=50)
    optionPricePreset: Literal['BID','ASK','MID','LAST','CUSTOM'] = 'MID'
    callPriceBasis: Basis = 'MID'
    putPriceBasis: Basis = 'MID'
    stockReferenceBasis: Basis = 'MID'
    refreshSeconds: Literal[10] = 10
    collectionEnabled: bool = True
    rate: float = Field(.05, ge=-1, le=2)
    rateLabel: str = '人工假设，非实时利率'
    timeRule: Literal['timestamp','days365'] = 'timestamp'
    remainingDays: float | None = Field(None, ge=0, le=36500)
    pricingAt: datetime | None = None
    timeAssumption: str | None = None
    maxAgeSeconds: float = Field(5, gt=0)
    maxSkewSeconds: float = Field(1, ge=0)
    statsAlternative: Literal['greater','less','two-sided'] = 'greater'
    deltaBps: float = 0
    alpha: float = Field(.05, gt=0, lt=1)
    minMinutes: int = Field(30, ge=30, le=60)
    maxMinutes: int = Field(60, ge=30, le=60)
    minSlots: int = Field(4, ge=4, le=6)
    family: str = 'primary'
    dividendPV: float | None = Field(None, ge=0)
    @field_validator('symbol','underlyingId',mode='before')
    @classmethod
    def strip_symbol(cls,v): return v.strip() if isinstance(v,str) else v
    @field_validator('exchangeTimezone')
    @classmethod
    def valid_zone(cls,v): ZoneInfo(v); return v
    @field_validator('pricingAt')
    @classmethod
    def aware(cls,v):
        if v and v.tzinfo is None: raise ValueError('时间必须包含时区')
        return v
    @model_validator(mode='after')
    def consistent(self):
        if self.provider=='manual' and self.dataMode!='MANUAL': raise ValueError('人工源必须使用 MANUAL 流')
        if self.provider=='mock' and self.dataMode!='MOCK': raise ValueError('模拟源必须使用 MOCK 流')
        if self.provider in ('http','futu') and self.dataMode!='API': raise ValueError('联网行情源必须使用 API 流')
        if self.provider=='futu' and (self.underlyingId.removeprefix('US.') not in FUTU_SYMBOLS or self.currency!='USD' or self.exchangeTimezone!='America/New_York'):
            raise ValueError('富途支持已配置的美股/ETF标的，币种 USD，交易所时区 America/New_York')
        if self.expiryMode=='manual' and not self.expiration: raise ValueError('请选择到期日')
        if self.timeRule=='days365' and self.remainingDays is None: raise ValueError('请输入剩余天数')
        if self.minMinutes>self.maxMinutes: raise ValueError('统计窗口上限小于下限')
        return self
class Quote(Model):
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    reportedMid: float | None = None
    bidAt: datetime | None = None
    askAt: datetime | None = None
    lastAt: datetime | None = None
    reportedMidAt: datetime | None = None
    bidSize: float | None = None
    askSize: float | None = None
    lastSize: float | None = None
    source: str
    feedType: Feed = 'UNKNOWN'
    receivedAt: datetime = Field(default_factory=utcnow)
    ingestedAt: datetime = Field(default_factory=utcnow)
    sequence: str | None = None
    unit: Literal['PER_SHARE','PER_CONTRACT'] = 'PER_SHARE'
    @field_validator('bidAt','askAt','lastAt','reportedMidAt','receivedAt','ingestedAt')
    @classmethod
    def aware(cls,v):
        if v and v.tzinfo is None: raise ValueError('报价时间必须带时区')
        return v.astimezone(timezone.utc) if v else v
class Contract(Model):
    id: str
    underlyingId: str
    expiration: str
    strike: Decimal = Field(gt=0)
    right: Literal['C','P']
    currency: str = 'USD'
    exercise: Literal['AMERICAN','EUROPEAN'] = 'AMERICAN'
    settlement: str = 'PHYSICAL'
    multiplier: Decimal = Decimal('100')
    deliverable: str = '100_SHARES'
    adjusted: bool = False
    pricingAt: datetime | None = None
    lastTradingAt: datetime | None = None
    exerciseCutoffAt: datetime | None = None
    @field_validator('pricingAt','lastTradingAt','exerciseCutoffAt')
    @classmethod
    def aware(cls,v):
        if v and v.tzinfo is None: raise ValueError('合约时间必须带时区')
        return v.astimezone(timezone.utc) if v else v
class Leg(Model):
    contract: Contract
    quote: Quote
class Snapshot(Model):
    monitorId: str
    valuationAt: datetime
    underlying: Quote
    contracts: list[Contract] = []
    legs: list[Leg] = []
    provider: str
    marketOpen: bool = False
    optionSessionOpen: bool = False
    marketReason: str = 'UNKNOWN_SESSION'
    historical: bool = False
    @field_validator('valuationAt')
    @classmethod
    def aware(cls,v):
        if v.tzinfo is None: raise ValueError('估值时间必须带时区')
        return v.astimezone(timezone.utc)
class Capabilities(Model):
    provider: str
    configured: bool
    batchLimit: int = Field(100,gt=0)
    feedType: Feed = 'UNKNOWN'
    protocol: str = 'option-monitor-http-v1'
class MarketState(Model):
    marketOpen: bool
    optionSessionOpen: bool
    reason: str
