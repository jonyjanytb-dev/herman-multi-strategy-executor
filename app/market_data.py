from __future__ import annotations

import logging
import time
from typing import Callable, Optional
import requests
from .models import Candle
from .okx_client import OKXClient

log=logging.getLogger(__name__)
MAINNET_INFO="https://api.hyperliquid.xyz/info"; TESTNET_INFO="https://api.hyperliquid-testnet.xyz/info"; MINUTE_MS=60_000


class HyperliquidMarketData:
    def __init__(self,network,coin,interval="1m",timeout=15.0):
        self.url=MAINNET_INFO if network=="mainnet" else TESTNET_INFO; self.coin=coin; self.interval=interval; self.timeout=timeout; self.session=requests.Session(); self._cache=[]; self._last_success_minute=None; self._retry_after_ms=0
    def _request(self,start,end):
        payload={"type":"candleSnapshot","req":{"coin":self.coin,"interval":self.interval,"startTime":start,"endTime":end}}
        r=self.session.post(self.url,json=payload,timeout=self.timeout)
        if r.status_code==429:
            try:retry=max(30.0,float(r.headers.get("Retry-After","")))
            except Exception:retry=60.0
            self._retry_after_ms=int(time.time()*1000+retry*1000); raise RuntimeError(f"Hyperliquid REST rate limited (429); cooling down for {retry:.0f}s")
        r.raise_for_status(); raw=r.json(); rows=[Candle(int(x["t"]),int(x["T"]),float(x["o"]),float(x["h"]),float(x["l"]),float(x["c"]),float(x.get("v",0))) for x in raw]; rows.sort(key=lambda x:x.t); return rows
    @staticmethod
    def _closed(rows,now): return [x for x in rows if x.T<now]
    def _bootstrap(self,count,now):
        windows=[8*60*MINUTE_MS,24*60*MINUTE_MS,72*60*MINUTE_MS,7*24*60*MINUTE_MS]; best=[]
        for i,w in enumerate(windows):
            rows=self._closed(self._request(now-w,now),now)
            if len(rows)>len(best):best=rows
            if len(best)>=count:break
            if i<len(windows)-1:time.sleep(1)
        return best[-count:]
    def _incremental(self,count,now):
        start=max(self._cache[-1].t-3*MINUTE_MS,now-10*MINUTE_MS); fresh=self._closed(self._request(start,now),now); merged={x.t:x for x in self._cache}; merged.update({x.t:x for x in fresh}); return sorted(merged.values(),key=lambda x:x.t)[-count:]
    def fetch_recent(self,count=320):
        now=int(time.time()*1000); bucket=now//MINUTE_MS
        if now<self._retry_after_ms:
            if self._cache:return self._cache[-count:]
            raise RuntimeError("Hyperliquid REST cooldown active")
        if self._cache and self._last_success_minute==bucket:return self._cache[-count:]
        try:new=self._bootstrap(count,now) if not self._cache else self._incremental(count,now)
        except RuntimeError as exc:
            if "429" in str(exc) and self._cache:log.warning("%s; using cached candles",exc); return self._cache[-count:]
            raise
        if new:self._cache=new; self._last_success_minute=bucket; self._retry_after_ms=0
        return self._cache[-count:]


class OKXMarketData:
    def __init__(self,inst_id,interval="1m",timeout=15.0,base_url="https://www.okx.com",retry_attempts=3,client:Optional[OKXClient]=None,clock_ms:Optional[Callable[[],int]]=None):
        self.inst_id=inst_id; self.interval=interval; self.client=client or OKXClient(base_url=base_url,timeout=timeout,retry_attempts=retry_attempts); self._clock_ms=clock_ms or (lambda:int(time.time()*1000)); self._cache=[]; self._last_success_minute=None
    def _request(self,limit):
        rows=self.client.get_public("/api/v5/market/candles",{"instId":self.inst_id,"bar":self.interval,"limit":str(limit)}); out=[]
        for row in rows:
            if len(row)<9 or str(row[8])!="1":continue
            t=int(row[0]); out.append(Candle(t,t+MINUTE_MS-1,float(row[1]),float(row[2]),float(row[3]),float(row[4]),float(row[5])))
        out.sort(key=lambda x:x.t); return out
    def fetch_recent(self,count=320):
        now=self._clock_ms(); bucket=now//MINUTE_MS
        if self._cache and self._last_success_minute==bucket:return self._cache[-count:]
        limit=min(300,count if not self._cache else max(10,min(count,100))); fresh=self._request(limit); merged={x.t:x for x in self._cache}; merged.update({x.t:x for x in fresh}); self._cache=sorted(merged.values(),key=lambda x:x.t)[-count:]
        expected=(bucket-1)*MINUTE_MS
        if self._cache and self._cache[-1].t>=expected:self._last_success_minute=bucket
        return self._cache[-count:]
