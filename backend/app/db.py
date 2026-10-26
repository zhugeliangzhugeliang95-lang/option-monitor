import hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import Index, create_engine, event, String, Integer, Text, JSON, Boolean, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
ROOT=Path(__file__).resolve().parents[2]
DB_PATH=Path(os.getenv('DATABASE_PATH',str(ROOT/'data/monitor.sqlite3')))
DB_PATH.parent.mkdir(parents=True,exist_ok=True)
# WAL never shrinks after a checkpoint on its own; cap what stays on disk (docs/API.md, 存储).
JOURNAL_SIZE_LIMIT=64*1024*1024
engine=create_engine('sqlite:///'+str(DB_PATH),connect_args={'timeout':15})
@event.listens_for(engine,'connect')
def pragmas(connection,_):
    connection.execute('PRAGMA journal_mode=WAL'); connection.execute('PRAGMA busy_timeout=15000'); connection.execute('PRAGMA foreign_keys=ON'); connection.execute(f'PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT}')
Session=sessionmaker(engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
class Monitor(Base):
    __tablename__='monitors'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    active:Mapped[bool]=mapped_column(Boolean,default=True)
    config_id:Mapped[str]=mapped_column(String)
    refresh_requested:Mapped[bool]=mapped_column(Boolean,default=False)
class RunConfig(Base):
    __tablename__='run_configs'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    monitor_id:Mapped[str]=mapped_column(String,index=True)
    version:Mapped[int]=mapped_column(Integer)
    hash:Mapped[str]=mapped_column(String)
    payload:Mapped[dict]=mapped_column(JSON)
    created_at:Mapped[str]=mapped_column(String)
class Record:
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    monitor_id:Mapped[str]=mapped_column(String,index=True)
    config_id:Mapped[str]=mapped_column(String,index=True)
    created_at:Mapped[str]=mapped_column(String,index=True)
    payload:Mapped[dict]=mapped_column(JSON)
class RawSnapshot(Record,Base):
    __tablename__='raw_snapshots'
    fingerprint:Mapped[str]=mapped_column(String,index=True)
class PairResult(Record,Base):
    __tablename__='pair_results'
    snapshot_id:Mapped[int]=mapped_column(Integer,index=True)
class AggregateResult(Record,Base):
    __tablename__='aggregate_results'
    __table_args__=(Index('ix_aggregate_results_monitor_config_id','monitor_id','config_id','id'),)
    snapshot_id:Mapped[int]=mapped_column(Integer,index=True)
class ProviderStatus(Record,Base): __tablename__='provider_status'
class WorkerState(Base):
    __tablename__='worker_state'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    payload:Mapped[dict]=mapped_column(JSON)
class ResultOutbox(Base):
    __tablename__='result_outbox'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    payload:Mapped[dict]=mapped_column(JSON)
    created_at:Mapped[str]=mapped_column(String)

def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def nowstr(): return datetime.now(timezone.utc).isoformat()
def record(cls,monitor,config,payload,**kw): return cls(monitor_id=monitor,config_id=config,created_at=nowstr(),payload=payload,**kw)
