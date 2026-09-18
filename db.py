# -*- coding: utf-8 -*-
"""
stock-radar · 数据库层（MySQL / SQLite 双后端自适应）

默认回退本地 SQLite（零依赖）；当 db_config.MYSQL_ENABLED=True 且 pymysql 可用时，
自动切换到 MySQL（默认 127.0.0.1:3306/game）。

表结构：
  stock_users    用户账户表
  stock_tabs     每个用户自己的页签列表（支持空页签）
  stock_datas    每个用户的自选/持仓数据（按 user_id 强隔离）
  stock_sessions 登录会话 token（用于"记住登录状态"）

设计要点：
  - 所有数据操作都以 user_id 为第一过滤条件，天然实现"用户数据隔离"。
  - SQL 统一用 '?' 占位符书写，运行时由 _ph() 在 MySQL 下转成 '%s'，
    并 INSERT OR IGNORE -> INSERT IGNORE，从而一套代码兼容两种引擎。
  - stock_datas 是唯一承载用户业务数据的表（即需求里的"用户数据表"）。
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

try:
    import pymysql
    from pymysql.cursors import DictCursor
    from pymysql.err import IntegrityError as _MyIntegrityError
except Exception:  # pymysql 未安装
    pymysql = None
    DictCursor = None
    _MyIntegrityError = Exception

import db_config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "stock_radar.db")
LEGACY_JSON = os.path.join(BASE_DIR, "stock_watch_config.json")

DEFAULT_TABS = ("自选", "持仓")

SESSION_EXPIRE_DAYS = 30


# ============================================================
# 引擎选择
# ============================================================
ENGINE = "mysql" if (db_config.MYSQL_ENABLED and pymysql) else "sqlite"

if ENGINE == "mysql":
    DB_INFO = "mysql://{user}@{host}:{port}/{db}".format(
        user=db_config.MYSQL_USER or "<user>",
        host=db_config.MYSQL_HOST,
        port=db_config.MYSQL_PORT,
        db=db_config.MYSQL_DB,
    )
else:
    DB_INFO = DB_PATH

if ENGINE == "sqlite":
    IntegrityError = sqlite3.IntegrityError
else:
    IntegrityError = _MyIntegrityError


def _ph(sql):
    """占位符 / 方言适配：MySQL 下 ? -> %s，且 INSERT OR IGNORE -> INSERT IGNORE。"""
    if ENGINE == "mysql":
        sql = sql.replace("INSERT OR IGNORE", "INSERT IGNORE")
        sql = sql.replace("?", "%s")
    return sql


# ============================================================
# 连接（统一包装：sqlite / mysql 都能用 conn.execute(...).fetch*() 风格）
# ============================================================
class _Conn:
    """统一包装：SQLite / MySQL 都返回可 fetch 的 cursor。
    MySQL 下每次 execute 都新建一个 cursor 并返回它，
    既避免 pymysql 的 execute 返回 int 导致 .fetchone() 崩溃，
    也避免复用同一 cursor 时 rowcount / lastrowid 相互覆盖。"""

    def __init__(self, raw):
        self._raw = raw
        self._closed = False

    def execute(self, sql, params=()):
        sql = _ph(sql)
        if ENGINE == "mysql":
            cur = self._raw.cursor()
            cur.execute(sql, params)
            return cur
        return self._raw.execute(sql, params)

    def commit(self):
        return self._raw.commit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._raw.close()
        except Exception:
            pass


def _conn():
    if ENGINE == "mysql":
        raw = pymysql.connect(
            host=db_config.MYSQL_HOST,
            port=db_config.MYSQL_PORT,
            user=db_config.MYSQL_USER,
            password=db_config.MYSQL_PASSWORD,
            database=db_config.MYSQL_DB,
            charset="utf8mb4",
            cursorclass=DictCursor,
            connect_timeout=5,
        )
    else:
        raw = sqlite3.connect(DB_PATH, check_same_thread=False)
        raw.row_factory = sqlite3.Row
    return _Conn(raw)


# ============================================================
# 建表（两种引擎各自 DDL）
# ============================================================
_SQLITE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS stock_users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username     TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt         TEXT NOT NULL,
        created_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_tabs (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER NOT NULL,
        tab_name  TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, tab_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_datas (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        tab_name     TEXT NOT NULL,
        code         TEXT NOT NULL,
        name         TEXT,
        up_warning   REAL,
        down_warning REAL,
        buy_price    REAL,
        shares       REAL,
        created_at   TEXT,
        UNIQUE(user_id, tab_name, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_sessions (
        token      TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """,
]

_MYSQL_DDL = [
    """
    CREATE TABLE IF NOT EXISTS stock_users (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        username     VARCHAR(64) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        salt         VARCHAR(64) NOT NULL,
        created_at   VARCHAR(32) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_tabs (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id    INT NOT NULL,
        tab_name   VARCHAR(64) NOT NULL,
        created_at VARCHAR(32) NOT NULL,
        UNIQUE KEY uk_user_tab (user_id, tab_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_datas (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        user_id      INT NOT NULL,
        tab_name     VARCHAR(64) NOT NULL,
        code         VARCHAR(32) NOT NULL,
        name         VARCHAR(64),
        up_warning   DOUBLE,
        down_warning DOUBLE,
        buy_price    DOUBLE,
        shares       DOUBLE,
        created_at   VARCHAR(32),
        UNIQUE KEY uk_ud (user_id, tab_name, code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_sessions (
        token      VARCHAR(128) PRIMARY KEY,
        user_id    INT NOT NULL,
        created_at VARCHAR(32) NOT NULL,
        expires_at VARCHAR(32) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def init_db():
    """建表（幂等）。"""
    ddl = _MYSQL_DDL if ENGINE == "mysql" else _SQLITE_DDL
    conn = _conn()
    try:
        for stmt in ddl:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    # 兼容已存在的旧表：若没有 shares 列则补上（不影响新库）
    ensure_shares_column()


def ensure_shares_column():
    """为已有的 stock_datas 表补加 shares 列（幂等）。"""
    conn = _conn()
    try:
        if ENGINE == "mysql":
            rows = conn.execute("SHOW COLUMNS FROM stock_datas").fetchall()
            cols = {r["Field"] for r in rows}
            if "shares" not in cols:
                conn.execute("ALTER TABLE stock_datas ADD COLUMN shares DOUBLE")
        else:
            rows = conn.execute("PRAGMA table_info(stock_datas)").fetchall()
            cols = {r["name"] for r in rows}
            if "shares" not in cols:
                conn.execute("ALTER TABLE stock_datas ADD COLUMN shares REAL")
        conn.commit()
    finally:
        conn.close()


# ============================================================
# 时间工具
# ============================================================
def _now():
    return datetime.now(timezone.utc).isoformat()


def _expire(days=SESSION_EXPIRE_DAYS):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _is_expired(iso):
    try:
        return datetime.fromisoformat(iso) < datetime.now(timezone.utc)
    except Exception:
        return True


# ============================================================
# 用户
# ============================================================
def create_user(username, password_hash, salt):
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO stock_users (username, password_hash, salt, created_at) "
            "VALUES (?,?,?,?)",
            (username, password_hash, salt, _now()),
        )
        uid = cur.lastrowid
        conn.commit()
        # 注册即创建默认页签
        for t in DEFAULT_TABS:
            _add_tab_raw(conn, uid, t)
        conn.commit()
        return uid
    finally:
        conn.close()


def get_user_by_username(username):
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM stock_users WHERE username=?", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(uid):
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM stock_users WHERE id=?", (uid,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_users():
    conn = _conn()
    try:
        return conn.execute("SELECT COUNT(*) AS cnt FROM stock_users").fetchone()["cnt"]
    finally:
        conn.close()


def change_password(uid, password_hash, salt):
    conn = _conn()
    try:
        conn.execute(
            "UPDATE stock_users SET password_hash=?, salt=? WHERE id=?",
            (password_hash, salt, uid),
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# 会话（token）
# ============================================================
def create_session(token, uid):
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO stock_sessions (token, user_id, created_at, expires_at) "
            "VALUES (?,?,?,?)",
            (token, uid, _now(), _expire()),
        )
        conn.commit()
    finally:
        conn.close()


def get_session(token):
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM stock_sessions WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if _is_expired(d["expires_at"]):
            delete_session(token)
            return None
        return d
    finally:
        conn.close()


def delete_session(token):
    conn = _conn()
    try:
        conn.execute("DELETE FROM stock_sessions WHERE token=?", (token,))
        conn.commit()
    finally:
        conn.close()


def delete_user_sessions(uid):
    conn = _conn()
    try:
        conn.execute("DELETE FROM stock_sessions WHERE user_id=?", (uid,))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# 页签（每用户）
# ============================================================
def _add_tab_raw(conn, uid, tab_name):
    conn.execute(
        "INSERT OR IGNORE INTO stock_tabs (user_id, tab_name, created_at) VALUES (?,?,?)",
        (uid, tab_name, _now()),
    )


def get_tabs(uid):
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT tab_name FROM stock_tabs WHERE user_id=? ORDER BY id", (uid,)
        ).fetchall()
        return [r["tab_name"] for r in rows]
    finally:
        conn.close()


def has_tab(uid, tab_name):
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM stock_tabs WHERE user_id=? AND tab_name=?",
            (uid, tab_name),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def add_tab(uid, tab_name):
    """返回 False 表示已存在。"""
    if has_tab(uid, tab_name):
        return False
    conn = _conn()
    try:
        _add_tab_raw(conn, uid, tab_name)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_tab(uid, tab_name):
    conn = _conn()
    try:
        r1 = conn.execute(
            "DELETE FROM stock_tabs WHERE user_id=? AND tab_name=?",
            (uid, tab_name),
        )
        conn.execute(
            "DELETE FROM stock_datas WHERE user_id=? AND tab_name=?",
            (uid, tab_name),
        )
        conn.commit()
        return r1.rowcount > 0
    finally:
        conn.close()


# ============================================================
# 用户数据（自选/持仓，强隔离）
# ============================================================
def get_user_config(uid):
    """返回 { tab_name: [ {code,name,up_warning,down_warning,buy_price}, ... ] }"""
    conn = _conn()
    try:
        tabs = get_tabs(uid)
        cfg = {t: [] for t in tabs}
        rows = conn.execute(
            "SELECT tab_name, code, name, up_warning, down_warning, buy_price, shares "
            "FROM stock_datas WHERE user_id=?", (uid,)
        ).fetchall()
        for r in rows:
            cfg.setdefault(r["tab_name"], []).append({
                "code": r["code"],
                "name": r["name"],
                "up_warning": r["up_warning"],
                "down_warning": r["down_warning"],
                "buy_price": r["buy_price"],
                "shares": r["shares"],
            })
        return cfg
    finally:
        conn.close()


def add_stock(uid, tab_name, stock):
    """stock: {code, name, up_warning, down_warning, buy_price}
    返回 False 表示已存在或页签不存在。"""
    if not has_tab(uid, tab_name):
        return False
    conn = _conn()
    try:
        try:
            conn.execute(
                "INSERT INTO stock_datas "
                "(user_id, tab_name, code, name, up_warning, down_warning, buy_price, shares, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, tab_name, stock["code"], stock.get("name"),
                 stock.get("up_warning"), stock.get("down_warning"),
                 stock.get("buy_price"), stock.get("shares"), _now()),
            )
            conn.commit()
            return True
        except IntegrityError:
            # 唯一约束 (user_id, tab_name, code) 冲突
            return False
    finally:
        conn.close()


def update_stock(uid, tab_name, code, updates):
    """updates: {up_warning, down_warning, buy_price, shares} 中需要更新的字段。
    只更新传入的字段，未传入的字段保持原值（避免误清空）。
    返回更新后的股票 dict，找不到返回 None。"""
    allowed = ("up_warning", "down_warning", "buy_price", "shares")
    fields = [(k, v) for k, v in updates.items() if k in allowed]
    if not fields:
        return None
    conn = _conn()
    try:
        set_clause = ", ".join(f"{k}=?" for k, _ in fields)
        params = [v for _, v in fields] + [uid, tab_name, code]
        cur = conn.execute(
            f"UPDATE stock_datas SET {set_clause} "
            f"WHERE user_id=? AND tab_name=? AND code=?",
            params,
        )
        if cur.rowcount == 0:
            return None
        conn.commit()
        row = conn.execute(
            "SELECT code, name, up_warning, down_warning, buy_price, shares "
            "FROM stock_datas WHERE user_id=? AND tab_name=? AND code=?",
            (uid, tab_name, code),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_stock(uid, tab_name, code):
    conn = _conn()
    try:
        r = conn.execute(
            "DELETE FROM stock_datas WHERE user_id=? AND tab_name=? AND code=?",
            (uid, tab_name, code),
        )
        conn.commit()
        return r.rowcount > 0
    finally:
        conn.close()


# ============================================================
# 旧版 JSON 一次性迁移
# ============================================================
def migrate_legacy_json(default_password="admin123"):
    """
    首次启动且数据库为空、且存在旧版 stock_watch_config.json 时：
    自动创建 admin 账号并把旧数据导入，避免丢失历史自选/持仓。
    返回 (username, password) 或 None。
    """
    import json
    import auth  # 同级模块

    if count_users() > 0:
        return None
    if not os.path.exists(LEGACY_JSON):
        return None

    try:
        with open(LEGACY_JSON, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except Exception as e:
        print(f"[migrate] 读取旧配置失败: {e}")
        return None

    if not isinstance(legacy, dict) or not legacy:
        return None

    from auth import hash_password
    salt, h = hash_password(default_password)
    uid = create_user("admin", h, salt)

    conn = _conn()
    try:
        for tab_name, stocks in legacy.items():
            # 默认页签（自选/持仓）注册时已创建，无需再建；非默认页签需先建
            if tab_name not in DEFAULT_TABS:
                _add_tab_raw(conn, uid, tab_name)
                conn.commit()
            for s in stocks:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO stock_datas "
                        "(user_id, tab_name, code, name, up_warning, down_warning, buy_price, shares, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (uid, tab_name, s.get("code"), s.get("name"),
                         s.get("up_warning"), s.get("down_warning"),
                         s.get("buy_price"), s.get("shares"), _now()),
                    )
                except Exception:
                    pass
            conn.commit()
    finally:
        conn.close()

    print("=" * 62)
    print("⚠️  检测到旧版配置，已自动迁移到数据库")
    print(f"    默认账号: admin")
    print(f"    默认密码: {default_password}  （请尽快在设置中修改！）")
    print("=" * 62)
    return ("admin", default_password)
