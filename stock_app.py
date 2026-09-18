# -*- coding: utf-8 -*-
"""
stock-radar · 股票雷达服务端（FastAPI + WebSocket + 用户体系）

- WebSocket /ws?token=xxx    每秒推送当前用户行情
- REST      /api/*           管理页签与股票（需登录）
- REST      /api/auth/*      注册 / 登录 / 登出 / 当前用户 / 改密
- 行情源：腾讯财经 http://qt.gtimg.cn/q=sz000001,sh600000
- 默认端口 5001

数据隔离：所有业务数据按 user_id 落库（stock_users / stock_tabs / stock_datas），
          接口与实时推送均按登录用户过滤，用户之间互不可见。

依赖：
    pip install -r requirements.txt

启动:
    python stock_app.py
"""
import asyncio
import copy
import os
import sys
import threading
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

import db
import auth


# 调试开关：默认关闭实时/调试日志（避免轮询刷屏）；
# 排查行情问题时在环境变量设置 SR_DEBUG=1 打开。
SR_DEBUG = os.environ.get("SR_DEBUG", "0") == "1"


# ============================================================
# 路径 & 运行参数
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
if not os.path.isdir(FRONTEND_DIR):
    FRONTEND_DIR = BASE_DIR

CONFIG_PATH = os.path.join(BASE_DIR, "stock_watch_config.json")

HOST = os.environ.get("STOCK_RADAR_HOST", "0.0.0.0")
PORT = int(os.environ.get("STOCK_RADAR_PORT", "5001"))

CERT_FILE = os.environ.get("STOCK_RADAR_CERT", os.path.join(BASE_DIR, "cert.pem"))
KEY_FILE  = os.environ.get("STOCK_RADAR_KEY",  os.path.join(BASE_DIR, "key.pem"))
USE_HTTPS = os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)


# ============================================================
# 工具函数
# ============================================================
def model_to_dict(model) -> dict:
    """兼容 pydantic v1 / v2"""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def normalize_code(code: str) -> str:
    """去掉 sh/sz/bj 前缀，返回纯 6 位数字"""
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    return c


def to_tencent_code(code: str) -> str:
    """
    把用户输入转换为腾讯行情代码：sz000001 / sh600000 / bj430047 / sz159792
    """
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.startswith(("15", "16", "18")):
        return "sz" + c
    if c.startswith("5"):
        return "sh" + c
    if c.startswith("6"):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    if c.startswith(("4", "8")):
        return "bj" + c
    return "sh" + c


# ============================================================
# 行情抓取（腾讯财经）
# ============================================================
class DataFetcher:
    BATCH_SIZE = 50
    TX_URL = "http://qt.gtimg.cn/q="
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    async def fetch_quotes(self, codes: list) -> dict:
        if not codes:
            return {}
        codes = [str(c).strip() for c in codes if str(c).strip()]
        results = {}
        loop = asyncio.get_event_loop()
        for i in range(0, len(codes), self.BATCH_SIZE):
            batch = codes[i:i + self.BATCH_SIZE]
            part = await loop.run_in_executor(None, self._fetch_sync, batch)
            results.update(part)
        return results

    @classmethod
    def _fetch_sync(cls, codes: list) -> dict:
        mapping = {}
        for c in codes:
            mapping[to_tencent_code(c)] = c

        url = cls.TX_URL + ",".join(mapping.keys())
        if SR_DEBUG:
            print(f"[fetcher] GET {url}")

        try:
            resp = requests.get(url, headers=cls.HEADERS, timeout=5)
            resp.encoding = "gbk"
            text = resp.text
            if SR_DEBUG:
                print(f"[fetcher] status={resp.status_code} len={len(text)}")
        except Exception as e:
            print(f"[fetcher] 腾讯接口请求失败: {type(e).__name__}: {e}")
            if SR_DEBUG:
                traceback.print_exc()
            return {}

        result = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or "=" not in line or not line.startswith("v_"):
                continue
            try:
                var_part, data_part = line.split("=", 1)
                tx_code = var_part.replace("v_", "").strip()
                data = data_part.strip().rstrip(";").strip('"')
                if not data:
                    continue

                fields = data.split("~")
                if len(fields) < 35:
                    continue

                user_code = mapping.get(tx_code, normalize_code(tx_code))
                name = fields[1].strip() or user_code

                def fnum(idx):
                    v = fields[idx] if idx < len(fields) else ""
                    if v in ("", None):
                        return 0.0
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        return 0.0

                price = fnum(3)
                pre_close = fnum(4)
                high = fnum(33)
                low = fnum(34)

                if price == 0.0 and pre_close:
                    price = pre_close

                change_pct = (price - pre_close) / pre_close * 100 if pre_close else 0.0
                change_amount = price - pre_close

                q = {
                    "name": name,
                    "price": price,
                    "pre_close": pre_close,
                    "low": low,
                    "high": high,
                    "change_pct": round(change_pct, 2),
                    "change_amount": round(change_amount, 3),
                    "valid": pre_close != 0,
                }
                result[user_code] = q
                result[normalize_code(tx_code)] = q
            except Exception as e:
                if SR_DEBUG:
                    print(f"[fetcher] 解析失败 [{line[:80]}]: {e}")
        return result


# ============================================================
# 预警管理（按用户作用域隔离）
# ============================================================
class AlertManager:
    def __init__(self):
        self._state: dict = {}

    def check(self, scope: str, tab_name: str, stock: dict, quote: dict) -> list:
        alerts = []
        code = stock["code"]
        name = stock.get("name") or quote.get("name") or code
        price = quote.get("price", 0.0)
        if not price:
            return alerts

        key = f"{scope}_{tab_name}_{normalize_code(code)}"
        st = self._state.setdefault(key, {"up": False, "down": False})

        up = stock.get("up_warning")
        if up is not None:
            if price >= up:
                if not st["up"]:
                    st["up"] = True
                    alerts.append({
                        "type": "up", "color": "red",
                        "title": "⚠️ 股票预警提示 ⚠️",
                        "message": f"{name}({code})\n当前价格: {price:.2f}元\n预警涨价: {up:.2f}元",
                    })
            else:
                st["up"] = False

        down = stock.get("down_warning")
        if down is not None:
            if price <= down:
                if not st["down"]:
                    st["down"] = True
                    alerts.append({
                        "type": "down", "color": "green",
                        "title": "⚠️ 股票预警提示 ⚠️",
                        "message": f"{name}({code})\n当前价格: {price:.2f}元\n预警跌价: {down:.2f}元",
                    })
            else:
                st["down"] = False

        return alerts

    def clear(self, scope: str, tab_name: str, code: str):
        self._state.pop(f"{scope}_{tab_name}_{normalize_code(code)}", None)

    def clear_tab(self, scope: str, tab_name: str):
        prefix = f"{scope}_{tab_name}_"
        for key in [k for k in self._state if k.startswith(prefix)]:
            self._state.pop(key, None)

    def prune(self, valid_keys: set):
        for key in [k for k in self._state if k not in valid_keys]:
            self._state.pop(key, None)


# ============================================================
# WebSocket 连接管理（按用户隔离广播）
# ============================================================
class WSManager:
    def __init__(self):
        self._conns: list = []   # [(ws, user_id), ...]

    async def connect(self, ws: WebSocket, user_id: int):
        await ws.accept()
        self._conns.append((ws, user_id))

    def disconnect(self, ws: WebSocket):
        self._conns = [(w, u) for (w, u) in self._conns if w is not ws]

    def active_user_ids(self) -> list:
        return list({u for (_, u) in self._conns})

    async def broadcast(self, user_id: int, message: dict):
        dead = []
        for (w, u) in self._conns:
            if u != user_id:
                continue
            try:
                await asyncio.wait_for(w.send_json(message), timeout=5)
            except Exception:
                dead.append(w)
        for w in dead:
            self.disconnect(w)


# ============================================================
# 请求模型
# ============================================================
class StockCreate(BaseModel):
    code: str
    up_warning: Optional[float] = None
    down_warning: Optional[float] = None
    buy_price: Optional[float] = None
    shares: Optional[float] = None


class StockUpdate(BaseModel):
    up_warning: Optional[float] = None
    down_warning: Optional[float] = None
    buy_price: Optional[float] = None
    shares: Optional[float] = None


class TabCreate(BaseModel):
    name: str


class RegisterBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


# ============================================================
# 单例
# ============================================================
fetcher = DataFetcher()
alerter = AlertManager()
ws_manager = WSManager()


# ============================================================
# 鉴权
# ============================================================
def authenticate(token: str):
    """返回 user dict 或 None"""
    if not token:
        return None
    sess = db.get_session(token)
    if not sess:
        return None
    return db.get_user_by_id(sess["user_id"])


def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.query_params.get("token")
    user = authenticate(token)
    if not user:
        raise HTTPException(401, "未登录或登录已过期")
    return user


# ============================================================
# 后台轮询（1s）—— 按用户分别推送，实现数据隔离
# ============================================================
async def update_loop():
    while True:
        try:
            await update_once()
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(1)


async def update_once():
    user_ids = ws_manager.active_user_ids()
    if not user_ids:
        return

    for uid in user_ids:
        tabs = db.get_user_config(uid)
        all_codes = sorted({s["code"] for stocks in tabs.values() for s in stocks})

        try:
            quotes = await fetcher.fetch_quotes(all_codes) if all_codes else {}
        except Exception:
            # 行情接口异常时，仍把结构（不含实时价）推下去，不让本轮整体失败
            quotes = {}

        payload = {}
        alerts = []
        for tab_name, stocks in tabs.items():
            payload[tab_name] = []
            for s in stocks:
                code = s["code"]
                q = quotes.get(code) or quotes.get(normalize_code(code)) or {}
                if q:
                    alerts.extend(alerter.check(str(uid), tab_name, s, q))
                payload[tab_name].append({
                    "code": code,
                    "name": s.get("name") or q.get("name") or code,
                    "up_warning": s.get("up_warning"),
                    "down_warning": s.get("down_warning"),
                    "buy_price": s.get("buy_price"),
                    "shares": s.get("shares"),
                    "quote": q,
                })

        await ws_manager.broadcast(uid, {"type": "quotes", "data": payload})
        for a in alerts:
            await ws_manager.broadcast(uid, {"type": "alert", "data": a})


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(update_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ============================================================
# FastAPI 应用
# ============================================================
app = FastAPI(title="stock-radar", lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp


app.add_middleware(SecurityHeadersMiddleware)


# ---------------- 认证 ----------------
@app.post("/api/auth/register")
def register(body: RegisterBody):
    username = body.username.strip()
    password = body.password
    if not username or len(username) < 2:
        raise HTTPException(400, "用户名至少 2 个字符")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if db.get_user_by_username(username):
        raise HTTPException(409, f"用户名 [{username}] 已被注册")

    salt, h = auth.hash_password(password)
    uid = db.create_user(username, h, salt)
    token = auth.generate_token()
    db.create_session(token, uid)
    return {"ok": True, "token": token, "username": username}


@app.post("/api/auth/login")
def login(body: LoginBody):
    username = body.username.strip()
    user = db.get_user_by_username(username)
    if not user or not auth.verify_password(body.password, user["salt"], user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    token = auth.generate_token()
    db.create_session(token, user["id"])
    return {"ok": True, "token": token, "username": user["username"]}


@app.get("/api/auth/me")
def me(user=Depends(get_current_user)):
    return {"ok": True, "username": user["username"], "user_id": user["id"]}


@app.post("/api/auth/logout")
def logout(request: Request, user=Depends(get_current_user)):
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if token:
        db.delete_session(token)
    return {"ok": True}


@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordBody, user=Depends(get_current_user)):
    old = body.old_password
    new = body.new_password
    if len(new) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if not auth.verify_password(old, user["salt"], user["password_hash"]):
        raise HTTPException(400, "原密码错误")
    salt, h = auth.hash_password(new)
    db.change_password(user["id"], h, salt)
    # 改密后让其它设备的会话失效，仅保留当前（调用方会重新登录拿新 token）
    db.delete_user_sessions(user["id"])
    return {"ok": True}


# ---------------- 业务数据（均需登录） ----------------
PROTECTED_TABS = ("自选", "持仓")


@app.get("/api/config")
def get_config(user=Depends(get_current_user)):
    return db.get_user_config(user["id"])


@app.post("/api/tabs")
def create_tab(body: TabCreate, user=Depends(get_current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "页签名称不能为空")
    if not db.add_tab(user["id"], name):
        raise HTTPException(409, f"页签 [{name}] 已存在")
    return {"ok": True, "name": name}


@app.delete("/api/tabs/{tab_name}")
def delete_tab(tab_name: str, user=Depends(get_current_user)):
    if tab_name in PROTECTED_TABS:
        raise HTTPException(400, "默认页签不可删除")
    if not db.delete_tab(user["id"], tab_name):
        raise HTTPException(404, "页签不存在")
    alerter.clear_tab(str(user["id"]), tab_name)
    return {"ok": True}


@app.post("/api/tabs/{tab_name}/stocks")
async def add_stock(tab_name: str, body: StockCreate, user=Depends(get_current_user)):
    code = body.code.strip()
    if not code:
        raise HTTPException(400, "股票代码不能为空")
    if not db.has_tab(user["id"], tab_name):
        raise HTTPException(404, "页签不存在")

    quotes = await fetcher.fetch_quotes([code])
    q = quotes.get(code) or quotes.get(normalize_code(code)) or {}
    pure_code = normalize_code(code)
    name = q.get("name") or code

    stock = {
        "code": pure_code,
        "name": name,
        "up_warning": body.up_warning,
        "down_warning": body.down_warning,
        "buy_price": body.buy_price,
        "shares": body.shares,
    }

    if not db.add_stock(user["id"], tab_name, stock):
        if not db.has_tab(user["id"], tab_name):
            raise HTTPException(404, "页签不存在")
        raise HTTPException(409, f"股票 {pure_code} 已在该页签中")

    return {**stock, "quote": q}


@app.patch("/api/tabs/{tab_name}/stocks/{code}")
def update_stock(tab_name: str, code: str, body: StockUpdate, user=Depends(get_current_user)):
    # 过滤掉未提供的字段（None），避免把已设置的股数/预警误清空
    updates = {k: v for k, v in model_to_dict(body).items() if v is not None}
    result = db.update_stock(user["id"], tab_name, code, updates)
    if result is None:
        raise HTTPException(404, "股票不存在")
    alerter.clear(str(user["id"]), tab_name, code)
    return result


@app.delete("/api/tabs/{tab_name}/stocks/{code}")
def delete_stock(tab_name: str, code: str, user=Depends(get_current_user)):
    if not db.delete_stock(user["id"], tab_name, code):
        raise HTTPException(404, "股票不存在")
    alerter.clear(str(user["id"]), tab_name, code)
    return {"ok": True}


# ---------------- WebSocket（需带 token） ----------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token = ws.query_params.get("token")
    user = authenticate(token)
    if not user:
        await ws.close(code=1008)   # Policy Violation：未授权
        return
    await ws_manager.connect(ws, user["id"])
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


# ---------------- 静态前端（必须放最后） ----------------
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


# ============================================================
# 启动自检 + 启动
# ============================================================
def startup_selftest():
    print("=" * 62)
    print("📈  stock-radar 股票雷达服务端（多用户版）")
    print(f"    Python : {sys.version.split()[0]}")
    print(f"    监听  : {'https' if USE_HTTPS else 'http'}://{HOST}:{PORT}")
    print(f"    前端  : {'https' if USE_HTTPS else 'http'}://127.0.0.1:{PORT}/")
    print(f"    数据库: {db.DB_INFO}")
    print(f"    行情源: 腾讯财经 qt.gtimg.cn")

    test_codes = ["sz000001", "sh600000", "sz159792", "sh510300"]
    try:
        r = requests.get("http://qt.gtimg.cn/q=" + ",".join(test_codes),
                         headers=DataFetcher.HEADERS, timeout=5)
        r.encoding = "gbk"
        text = r.text.strip()
        ok_count = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or '="' not in line:
                continue
            try:
                parts = line.split('"')[1].split("~")
                if len(parts) >= 5 and parts[1]:
                    if SR_DEBUG:
                        print(f"    ✓ {parts[2]} -> {parts[1]} 现价 {parts[3]}")
                    ok_count += 1
            except Exception:
                pass
        if ok_count == 0:
            print(f"    ⚠️  行情自检返回异常：{text[:120]}")
    except Exception as e:
        print(f"    ⚠️  行情自检失败：{type(e).__name__}: {e}")

    print("=" * 62)


if __name__ == "__main__":
    try:
        import uvicorn
    except ImportError:
        print("❌ 缺少 uvicorn，请先安装： pip install -r requirements.txt")
        raise SystemExit(1)

    # 初始化数据库 + 旧数据迁移
    db.init_db()
    db.migrate_legacy_json()

    startup_selftest()

    if USE_HTTPS:
        uvicorn.run(
            app, host=HOST, port=PORT,
            ssl_certfile=CERT_FILE, ssl_keyfile=KEY_FILE,
            log_level="info", access_log=False,
        )
    else:
        uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False)
