"""Local reference gateway. MOCK ONLY. No brokerage compatibility claim."""
from fastapi import FastAPI, HTTPException
from .providers import MockProvider
from .models import Config
from .logs import configure as _configure_logging
_configure_logging()  # uvicorn configures its loggers before importing the app; wrap them with redaction
app=FastAPI(title='MOCK reference quote gateway'); provider=MockProvider(); catalog={}
@app.get('/v1/capabilities')
async def capabilities(): return await provider.get_capabilities()
@app.get('/v1/market-state')
async def state(symbol:str,calendar:str='XNYS'): return await provider.get_market_state(Config(symbol=symbol,calendar=calendar))
@app.get('/v1/underlyings')
async def underlying(ids:str): return {'items':await provider.get_underlying_snapshots(ids.split(','))}
@app.get('/v1/expirations')
async def expirations(symbol:str,timezone:str='America/New_York',cursor:str|None=None): return {'items':await provider.list_expirations(Config(symbol=symbol,exchangeTimezone=timezone)),'nextCursor':None}
@app.get('/v1/contracts')
async def contracts(symbol:str,expiration:str,timezone:str='America/New_York',cursor:str|None=None):
    rows=await provider.list_option_contracts(Config(symbol=symbol,underlyingId=symbol,exchangeTimezone=timezone),expiration)
    catalog.update({c.id:c for c in rows}); start=int(cursor or 0)
    return {'items':rows[start:start+20],'nextCursor':str(start+20) if start+20<len(rows) else None}
@app.get('/v1/quotes')
async def quotes(ids:str):
    if any(i not in catalog for i in ids.split(',')): raise HTTPException(404,'Unknown contract: list contracts first')
    return {'items':await provider.get_option_quotes([catalog[i] for i in ids.split(',')])}
