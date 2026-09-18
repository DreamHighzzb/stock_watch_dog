import asyncio
import os
import time
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .models import StockCreate, StockUpdate, TabCreate
from .config_store import ConfigStore
from .data_fetcher import DataFetcher
from .alert_manager import AlertManager
from .ws_manager import WSManager

# ---------------- 路径 ----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
CONFIG_PATH = os.path.join(BASE_DIR, "stock_watch_config.json")

# ---------------- 单例组件 ----------------
config = ConfigStore(CONFIG_PATH)
fetcher = DataFetcher()
alerter = AlertManager()
ws_manager = WSManager()

# 单次轮询目标周期（秒）
POLL_INTERVAL = 1.0


# ---------------- 后台轮询任务 ----------------
async def update_loop():
    """按固定周期拉取行情，广播到所有前端（补偿 fetch 耗时，避免漂移）"""
    while True:
        start = time.monotonic()
        try:
            await update_once()
        except Exception:
            traceback.print_exc()
        elapsed = time.monotonic() - start
        await asyncio.sleep(max(0.0, POLL_INTERVAL - elapsed))


async def update_once():
    tabs = config.snapshot()
    all_codes = sorted({s["code"] for stocks in tabs.values() for s in stocks})

    # 即使没有股票也要广播（让前端 tabs 结构及时同步）
    if not all_codes:
        payload = {tab_name: [] for tab_name in tabs}
        await ws_manager.broadcast({"type": "quotes", "data": payload})
        # 清理无对应股票的历史预警状态
        alerter.prune(set())
        return

    quotes = await fetcher.fetch_quotes(all_codes)

    payload = {}
    alerts = []
    valid_keys = set()
    for tab_name, stocks in tabs.items():
        payload[tab_name] = []
        for s in stocks:
            code = s["code"]
            valid_keys.add(f"{tab_name}_{code}")
            q = quotes.get(code) or {}
            if q:
                alerts.extend(alerter.check(tab_name, s, q))
            payload[tab_name].append({
                "code": code,
                "name": s.get("name") or q.get("name") or code,
                "up_warning": s.get("up_warning"),
                "down_warning": s.get("down_warning"),
                "buy_price": s.get("buy_price"),
                "quote": q,
            })

    # 清理已不存在的股票预警状态
    alerter.prune(valid_keys)

    await ws_manager.broadcast({"type": "quotes", "data": payload})
    for a in alerts:
        await ws_manager.broadcast({"type": "alert", "data": a})


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


app = FastAPI(title="stock-radar", lifespan=lifespan)


# ---------------- REST API ----------------
@app.get("/api/config")
def get_config():
    """返回 { tab_name: [stocks] }"""
    return config.snapshot()


@app.post("/api/tabs")
def create_tab(body: TabCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "页签名称不能为空")
    if not config.add_tab(name):
        raise HTTPException(409, f"页签 [{name}] 已存在")
    return {"ok": True, "name": name}


@app.delete("/api/tabs/{tab_name}")
def delete_tab(tab_name: str):
    if tab_name in config.PROTECTED_TABS:
        raise HTTPException(400, "默认页签不可删除")
    if not config.delete_tab(tab_name):
        raise HTTPException(404, "页签不存在")
    alerter.clear_tab(tab_name)
    return {"ok": True}


@app.post("/api/tabs/{tab_name}/stocks")
async def add_stock(tab_name: str, body: StockCreate):
    code = body.code.strip()
    if not code:
        raise HTTPException(400, "股票代码不能为空")
    if not config.has_tab(tab_name):
        raise HTTPException(404, "页签不存在")

    quotes = await fetcher.fetch_quotes([code])
    if code not in quotes:
        raise HTTPException(400, f"股票代码 {code} 无效")

    stock = {
        "code": code,
        "name": quotes[code]["name"],
        "up_warning": body.up_warning,
        "down_warning": body.down_warning,
        "buy_price": body.buy_price,
    }
    if not config.add_stock(tab_name, stock):
        raise HTTPException(409, f"股票 {code} 已在该页签中")
    return stock


@app.patch("/api/tabs/{tab_name}/stocks/{code}")
def update_stock(tab_name: str, code: str, body: StockUpdate):
    result = config.update_stock(tab_name, code, body.model_dump())
    if result is None:
        raise HTTPException(404, "股票不存在")
    # 价格阈值变了，清掉预警去重状态，允许立即重新触发
    alerter.clear(tab_name, code)
    return result


@app.delete("/api/tabs/{tab_name}/stocks/{code}")
def delete_stock(tab_name: str, code: str):
    if not config.delete_stock(tab_name, code):
        raise HTTPException(404, "股票不存在")
    alerter.clear(tab_name, code)
    return {"ok": True}


# ---------------- WebSocket ----------------
WS_RECV_TIMEOUT = 30  # 秒；用于探测死连接


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            try:
                # 客户端不发消息，靠超时来探测连接是否还活着
                await asyncio.wait_for(ws.receive_text(), timeout=WS_RECV_TIMEOUT)
            except asyncio.TimeoutError:
                # 超时不算断开；发一个 ping 让底层探活
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


# ---------------- 静态前端（必须放最后） ----------------
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")