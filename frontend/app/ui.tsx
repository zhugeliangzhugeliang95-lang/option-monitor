'use client';
import { useEffect,useState } from 'react';
export function Field({title,children,note}:{title:string,children:React.ReactNode,note?:string}){return <label className="field"><span>{title}</span>{children}{note&&<small>{note}</small>}</label>}
export function Stat({label,value,sub,accent=false}:{label:string,value:string,sub?:string,accent?:boolean}){return <div className="metric"><span>{label}</span><strong className={accent?'accent':''}>{value}</strong><small>{sub}</small></div>}
export function useNow(){const [now,setNow]=useState(Date.now());useEffect(()=>{const t=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(t);},[]);return now;}
export function Connection({connected,heartbeatAt}:{connected:boolean,heartbeatAt?:string}){const now=useNow();const workerLive=heartbeatAt?(now-new Date(heartbeatAt).getTime())/1000<8:false;return <span className={'connection '+(connected&&workerLive?'live':'')}><i/>{!connected?'连接已断开，正在重连':workerLive?'Worker 在线':'采集中断'}</span>}
export function Stale({at,maxAge,fresh='',suffix=''}:{at?:string,maxAge:number,fresh?:string,suffix?:string}){const now=useNow();return <>{at&&(now-new Date(at).getTime())/1000>maxAge?'历史 / 过旧快照'+suffix:fresh}</>}
