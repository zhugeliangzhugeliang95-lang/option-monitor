from datetime import datetime,date,timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Leg(Model):
    code:str|None=None
    last:float|None=Field(None,ge=0,le=1e9)
    bid:float|None=Field(None,ge=0,le=1e9)
    ask:float|None=Field(None,ge=0,le=1e9)
    lastAt:datetime|None=None
    bookAt:datetime|None=None
    updatedAt:datetime|None=None
    underlying:str|None=None
    expiration:date|None=None
    currency:str='USD'
    multiplier:Decimal=Field(Decimal('100'),gt=0,le=1000000)
    exercise:Literal['AMERICAN','EUROPEAN','UNKNOWN']='AMERICAN'
    adjusted:bool=False
    @field_validator('lastAt','bookAt','updatedAt')
    @classmethod
    def zone(cls,v):
        if v is not None and v.tzinfo is None:raise ValueError('报价时刻必须带时区')
        return v.astimezone(timezone.utc) if v else v

class Row(Model):
    strike:Decimal=Field(gt=0,le=1e9)
    call:Leg|None=None
    put:Leg|None=None
    issues:list[str]=Field(default_factory=list,max_length=30)

class StudyInput(Model):
    symbol:str=Field(min_length=1,max_length=24,pattern=r'^[A-Za-z0-9._-]+$')
    spot:float|None=Field(None,gt=0,le=1e9)
    expiration:date
    valuationAt:datetime
    pricingAt:datetime|None=None
    rate:float=Field(.05,ge=-.5,le=1)
    timeRule:Literal['calendar','timestamp','days']='calendar'
    remainingDays:float|None=Field(None,ge=0,le=3650)
    maxAgeSeconds:float=Field(60,gt=0,le=86400)
    maxSkewSeconds:float=Field(5,ge=0,le=86400)
    rows:list[Row]=Field(min_length=1,max_length=500)
    @field_validator('valuationAt','pricingAt')
    @classmethod
    def zone(cls,v):
        if v is not None and v.tzinfo is None:raise ValueError('估值及定价时刻必须带时区')
        return v.astimezone(timezone.utc) if v else v
    @model_validator(mode='after')
    def rules(self):
        if len({r.strike for r in self.rows})!=len(self.rows):raise ValueError('行权价不能重复；每档最多一对 Call / Put')
        if self.timeRule=='days' and self.remainingDays is None:raise ValueError('请输入剩余天数')
        if self.timeRule=='timestamp' and self.pricingAt is None:raise ValueError('请输入定价参考时刻')
        if self.timeRule=='timestamp':
            if self.pricingAt.astimezone(ZoneInfo('America/New_York')).date()!=self.expiration:
                raise ValueError('定价参考时刻必须位于所选到期日（按纽约时间）')
            seconds=(self.pricingAt-self.valuationAt).total_seconds()
            if not 0<=seconds<=3650*86400:
                raise ValueError('定价参考时刻须不早于估值时刻，且相距不超过 3650 天')
        return self

class CompareRequest(Model):
    basis:Literal['LAST','MID']='LAST'
    strikes:list[Decimal]=Field(min_length=3,max_length=3)
    @field_validator('strikes')
    @classmethod
    def distinct(cls,v):
        if any(not k.is_finite() or k<=0 for k in v) or len(set(v))!=3:raise ValueError('请选择三个不同的正数行权价')
        return sorted(v)

class FutuRequest(Model):
    symbol:str=Field(min_length=1,max_length=16,pattern=r'^(US\.)?[A-Za-z][A-Za-z0-9.-]*$')
    rate:float=Field(.05,ge=-.5,le=1)
