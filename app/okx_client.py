from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlencode, urlparse

import requests


class OKXAPIError(RuntimeError):
    pass


class OKXClient:
    def __init__(self, base_url="https://www.okx.com", api_key="", secret_key="", passphrase="", demo=False, timeout=15.0, retry_attempts=3, session=None, timestamp: Optional[Callable[[], str]] = None, sleep: Callable[[float], None] = time.sleep):
        parsed=urlparse(base_url)
        if parsed.scheme!="https" or parsed.hostname not in {"www.okx.com","my.okx.com","app.okx.com"} or parsed.port not in {None,443} or parsed.username or parsed.password or parsed.path not in {"","/"} or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an official OKX HTTPS origin")
        self.base_url=base_url.rstrip("/"); self.api_key=api_key; self.secret_key=secret_key; self.passphrase=passphrase; self.demo=demo; self.timeout=timeout; self.retry_attempts=max(1,retry_attempts)
        self.session=session or requests.Session(); self._timestamp=timestamp or self._utc_timestamp; self._sleep=sleep

    @staticmethod
    def _utc_timestamp(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")
    @staticmethod
    def _query_path(path,params):
        if not params:return path
        clean={k:v for k,v in params.items() if v is not None and v!=""}; q=urlencode(clean)
        return f"{path}?{q}" if q else path

    def _private_headers(self,timestamp,method,request_path,body):
        if not self.api_key or not self.secret_key or not self.passphrase:raise OKXAPIError("OKX private request requires API key, secret key and passphrase")
        prehash=f"{timestamp}{method}{request_path}{body}".encode(); sig=base64.b64encode(hmac.new(self.secret_key.encode(),prehash,hashlib.sha256).digest()).decode()
        headers={"Content-Type":"application/json","OK-ACCESS-KEY":self.api_key,"OK-ACCESS-SIGN":sig,"OK-ACCESS-TIMESTAMP":timestamp,"OK-ACCESS-PASSPHRASE":self.passphrase}
        if self.demo:headers["x-simulated-trading"]="1"
        return headers

    @staticmethod
    def _validate_payload(payload):
        code=str(payload.get("code",""))
        if code!="0":raise OKXAPIError(f"OKX API error {code}: {payload.get('msg','')}")
        data=payload.get("data") or []
        for item in data:
            if isinstance(item,dict) and str(item.get("sCode","0")) not in {"","0"}:raise OKXAPIError(f"OKX order error {item.get('sCode')}: {item.get('sMsg','')}")
        return data

    def _request(self,method,path,params=None,body=None,private=False):
        method=method.upper(); request_path=self._query_path(path,params); body_text="" if body is None else json.dumps(body,separators=(",",":"),ensure_ascii=False); last=None
        for attempt in range(self.retry_attempts):
            ts=self._timestamp(); headers=self._private_headers(ts,method,request_path,body_text) if private else {"Content-Type":"application/json"}
            try:
                r=self.session.request(method=method,url=self.base_url+request_path,headers=headers,data=body_text or None,timeout=self.timeout)
                if r.status_code==429 or r.status_code>=500:raise OKXAPIError(f"OKX HTTP {r.status_code}: {r.text}")
                if r.status_code>=400:raise OKXAPIError(f"OKX HTTP {r.status_code}: {r.text}")
                return self._validate_payload(r.json())
            except (requests.RequestException,ValueError,OKXAPIError) as exc:
                last=exc; transient=isinstance(exc,requests.RequestException) or (isinstance(exc,OKXAPIError) and ("HTTP 429" in str(exc) or any(f"HTTP {x}" in str(exc) for x in range(500,600))))
                if not transient or attempt+1>=self.retry_attempts:break
                self._sleep(0.5*(2**attempt))
        if isinstance(last,OKXAPIError):raise last
        raise OKXAPIError(f"OKX request failed: {last}") from last

    def get_public(self,path,params=None): return self._request("GET",path,params=params,private=False)
    def get_private(self,path,params=None): return self._request("GET",path,params=params,private=True)
    def post_private(self,path,body): return self._request("POST",path,body=body,private=True)
