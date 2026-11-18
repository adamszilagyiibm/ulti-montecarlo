"""
wx.py - watsonx.ai hívások (IBM Cloud IAM token, szöveg + kép), fájlba kulcs NEM kerül.

A token az `ibmcloud iam oauth-tokens` parancsból jön (be kell jelentkezni: `ibmcloud login`),
vagy a WATSONX_TOKEN környezeti változóból. A projekt: WATSONX_PROJECT (alapértelmezés: precognox-embedding).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

BASE = os.getenv("WATSONX_URL", "https://eu-de.ml.cloud.ibm.com")
PROJECT = os.getenv("WATSONX_PROJECT", "c2c58680-3f5c-42f0-93ef-3ab103bb327d")
VISION_MODEL = os.getenv("WATSONX_VISION_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8")
TEXT_MODEL = os.getenv("WATSONX_TEXT_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8")

_tok: Dict[str, Any] = {"v": None, "t": 0.0}


def token() -> str:
    env = os.getenv("WATSONX_TOKEN")
    if env:
        return env
    if _tok["v"] and time.time() - _tok["t"] < 1800:   # az IAM token kb. 1 óráig érvényes
        return _tok["v"]
    out = subprocess.run(["ibmcloud", "iam", "oauth-tokens"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "IAM token" in line:
            _tok["v"], _tok["t"] = line.split()[-1], time.time()
            return _tok["v"]
    raise RuntimeError("Nincs IBM token: jelentkezz be (ibmcloud login) vagy állítsd be a WATSONX_TOKEN-t")


def image_part(png: bytes) -> Dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def chat(content: List[Dict[str, Any]], model: str, max_tokens: int = 2000, temperature: float = 0.0,
         retries: int = 5, seed: Optional[int] = None) -> str:
    body: Dict[str, Any] = {"model_id": model, "project_id": PROJECT, "max_tokens": max_tokens,
                            "temperature": temperature, "messages": [{"role": "user", "content": content}]}
    if seed is not None:
        body["seed"] = seed
    last = ""
    for k in range(retries):
        req = urllib.request.Request(
            f"{BASE}/ml/v1/text/chat?version=2024-12-01", data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=180))
            return d["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            last = f"{e.code} {e.read().decode()[:200]}"
            if e.code in (401,):
                _tok["v"] = None
            if e.code not in (401, 429, 500, 502, 503, 504):
                break
        except Exception as e:  # időtúllépés, hálózat
            last = f"{type(e).__name__}: {e}"
        time.sleep(3 * (k + 1))
    raise RuntimeError("watsonx hiba: " + last)


def text(prompt: str, model: str = TEXT_MODEL, **kw: Any) -> str:
    return chat([{"type": "text", "text": prompt}], model, **kw)


def vision(prompt: str, png: bytes, model: str = VISION_MODEL, **kw: Any) -> str:
    return chat([{"type": "text", "text": prompt}, image_part(png)], model, **kw)
