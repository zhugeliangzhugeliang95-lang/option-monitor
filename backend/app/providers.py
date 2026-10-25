from __future__ import annotations
import asyncio, hashlib, os, random
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Protocol
from zoneinfo import ZoneInfo
import httpx
import exchange_calendars as xcals
import pandas as pd
from .models import *

class ProviderError(Exception):
    def __init__(self,code,retry_after=0): self.code=code; self.retry_after=retry_after; super().__init__(code)
class Provider(Protocol):
    async def get_capabilities(self)->Capabilities: ...
    async def get_market_state(self,cfg:Config)->MarketState: ...
    async def get_underlying_snapshots(self,ids:list[str])->dict[str,Quote]: ...
    async def list_expirations(self,cfg:Config)->list[str]: ...
    async def list_option_contracts(self,cfg:Config,expiration:str)->list[Contract]: ...
    async def get_option_quotes(self,contracts:list[Contract])->dict[str,Quote]: ...
    async def close(self): ...

def exchange_day(cfg,now):
    """ISO trading date of `now` in the config's exchange timezone (caller supplies the clock)."""
    return now.astimezone(ZoneInfo(cfg.exchangeTimezone)).date().isoformat()

def live_expirations(dates,today):
    """Drop expirations before `today` (exchange date, ISO). today itself is 0DTE and kept."""
    return [d for d in dates if str(d)>=today]

def target_expiry(cfg,today):
    """sameDay -> today; manual -> the configured date, never substituted (even when expired)."""
    return today if cfg.expiryMode=='sameDay' else cfg.expiration

def regular_session(cfg,at):
    try:
        cal=xcals.get_calendar(cfg.calendar)
        return bool(cal.is_open_on_minute(pd.Timestamp(at).floor('min'),ignore_breaks=False))
    except (ValueError,KeyError): return False

class ManualProvider:
    async def get_capabilities(self): return Capabilities(provider='manual',configured=True,feedType='MANUAL')
    async def get_market_state(self,cfg): return MarketState(marketOpen=False,optionSessionOpen=False,reason='MANUAL_INPUT_REQUIRED')
    async def get_underlying_snapshots(self,ids): return {}
    async def list_expirations(self,cfg): return []
    async def list_option_contracts(self,cfg,expiration): return []
    async def get_option_quotes(self,contracts): return {}
    async def close(self): pass

class MockProvider:
    """Deterministic per 10-second event slot. Always MOCK, even behind HTTP."""
    def __init__(self,clock=utcnow,seed=20261018): self.clock=clock; self.seed=seed
    def at(self): return datetime.fromtimestamp(int(self.clock().timestamp()//10)*10,timezone.utc)
    def rng(self,key): return random.Random(int(hashlib.sha256(f'{self.seed}:{self.at()}:{key}'.encode()).hexdigest()[:16],16))
    def spot(self,key): return {'SPY':600,'QQQ':510,'AAPL':230}.get(key,150)+self.rng(key).uniform(-.8,.8)
    def quote(self,key,mid,spread=.04):
        at=self.at(); return Quote(bid=max(0,mid-spread/2),ask=mid+spread/2,last=mid,bidAt=at,askAt=at,lastAt=at,
            bidSize=100,askSize=120,lastSize=10,source='deterministic-reference-mock',feedType='MOCK',sequence=f'{key}:{at.isoformat()}')
    async def get_capabilities(self): return Capabilities(provider='mock',configured=True,feedType='MOCK')
    async def get_market_state(self,cfg): return MarketState(marketOpen=True,optionSessionOpen=True,reason='MOCK_SYNTHETIC_SESSION')
    async def get_underlying_snapshots(self,ids): return {key:self.quote(key,self.spot(key)) for key in ids}
    async def list_expirations(self,cfg): return [self.at().astimezone(ZoneInfo(cfg.exchangeTimezone)).date().isoformat()]
    async def list_option_contracts(self,cfg,expiration):
        # Synthetic metadata only: not a claim about any real option's expiration.
        at=self.at(); end=at+timedelta(days=2); center=round(self.spot(cfg.underlyingId)/5)*5
        return [Contract(id=f'{cfg.underlyingId}:{expiration}:{k}:{right}',underlyingId=cfg.underlyingId,expiration=expiration,strike=Decimal(k),right=right,currency=cfg.currency,pricingAt=end,lastTradingAt=end,exerciseCutoffAt=end+timedelta(hours=1)) for k in range(center-50,center+55,5) for right in ('C','P')]
    async def get_option_quotes(self,contracts):
        out={}
        for c in contracts:
            spot=self.spot(c.underlyingId); intrinsic=spot-float(c.strike); noise=self.rng(str(c.strike)).uniform(-.15,.15)
            mid=2+max(intrinsic,0)+noise if c.right=='C' else 2+max(-intrinsic,0)
            out[c.id]=self.quote(c.id,mid,.10)
        return out
    async def close(self): pass

class HttpJsonProvider:
    def __init__(self,transport=None):
        url=os.getenv('QUOTE_GATEWAY_URL','').rstrip('/')
        self.configured=bool(url)
        self.client=httpx.AsyncClient(base_url=url or 'http://127.0.0.1',timeout=7,transport=transport,
            headers={'Authorization':'Bearer '+os.environ['QUOTE_GATEWAY_KEY']} if os.getenv('QUOTE_GATEWAY_KEY') else {})
    async def request(self,path,params=None):
        if not self.configured: raise ProviderError('NOT_CONFIGURED')
        try:
            r=await self.client.get(path,params=params)
            if r.status_code==429:
                h=r.headers.get('Retry-After','10')
                try: wait=float(h)
                except ValueError:
                    try: wait=max(1,(parsedate_to_datetime(h)-utcnow()).total_seconds())
                    except (ValueError,TypeError): wait=10
                raise ProviderError('RATE_LIMITED',wait)
            if r.status_code in (401,403): raise ProviderError('PERMISSION_DENIED')
            if r.status_code==409: raise ProviderError('NOT_SUBSCRIBED')
            if not r.is_success: raise ProviderError(f'HTTP_{r.status_code}')
            return r.json()
        except httpx.TimeoutException: raise ProviderError('TIMEOUT') from None
        except (httpx.RequestError,ValueError): raise ProviderError('CONNECTION_OR_PROTOCOL_ERROR') from None
    async def pages(self,path,params):
        out=[]; cursor=None; seen=set()
        for _ in range(100):
            r=await self.request(path,{**params,**({'cursor':cursor} if cursor else {})})
            out.extend(r['items']); cursor=r.get('nextCursor')
            if not cursor: return out
            if cursor in seen: raise ProviderError('PAGINATION_LOOP')
            seen.add(cursor)
        raise ProviderError('PAGINATION_LIMIT')
    async def get_capabilities(self):
        if not self.configured: return Capabilities(provider='http',configured=False)
        return Capabilities.model_validate(await self.request('/v1/capabilities'))
    async def get_market_state(self,cfg): return MarketState.model_validate(await self.request('/v1/market-state',{'symbol':cfg.symbol,'calendar':cfg.calendar}))
    async def get_underlying_snapshots(self,ids):
        cap=await self.get_capabilities(); out={}
        for i in range(0,len(ids),cap.batchLimit):
            r=await self.request('/v1/underlyings',{'ids':','.join(ids[i:i+cap.batchLimit])})
            out.update({k:Quote.model_validate(v) for k,v in r['items'].items()})
        return out
    async def list_expirations(self,cfg): return await self.pages('/v1/expirations',{'symbol':cfg.underlyingId,'timezone':cfg.exchangeTimezone})
    async def list_option_contracts(self,cfg,expiration): return [Contract.model_validate(x) for x in await self.pages('/v1/contracts',{'symbol':cfg.underlyingId,'expiration':expiration,'timezone':cfg.exchangeTimezone})]
    async def get_option_quotes(self,contracts):
        cap=await self.get_capabilities(); out={}
        for i in range(0,len(contracts),cap.batchLimit):
            r=await self.request('/v1/quotes',{'ids':','.join(c.id for c in contracts[i:i+cap.batchLimit])})
            out.update({k:Quote.model_validate(v) for k,v in r['items'].items()})
        return out
    async def close(self): await self.client.aclose()

def provider_for(name):
    from .futu import FutuProvider
    return {'manual':ManualProvider,'mock':MockProvider,'http':HttpJsonProvider,'futu':FutuProvider}[name]()
