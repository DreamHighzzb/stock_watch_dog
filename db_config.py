# -*- coding: utf-8 -*-
"""
数据库连接配置 —— 把股票数据迁移到 127.0.0.1 上的 MySQL game 库时在此填写。

环境变量覆盖（优先级高于下方常量）：
  SR_DB_HOST     默认 127.0.0.1
  SR_DB_PORT     默认 3306
  SR_DB_USER     用户名（必填）
  SR_DB_PASSWORD 密码（必填）
  SR_DB_NAME     库名，默认 game
  SR_DB_ENABLED  设为 1 即启用 MySQL（否则回退本地 SQLite）

启用步骤：
  1. 填好上面的 MYSQL_USER / MYSQL_PASSWORD（或设环境变量）
  2. 把 MYSQL_ENABLED 改成 True（或设 SR_DB_ENABLED=1）
  3. 重跑 stock_start.sh 即可
"""
import os

MYSQL_HOST = os.environ.get("SR_DB_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("SR_DB_PORT", "3306"))
MYSQL_USER = os.environ.get("SR_DB_USER", "root")        # 占位：请填写或设环境变量 SR_DB_USER
MYSQL_PASSWORD = os.environ.get("SR_DB_PASSWORD", "xxx")  # 占位：请填写或设环境变量 SR_DB_PASSWORD
MYSQL_DB = os.environ.get("SR_DB_NAME", "game")

# 是否启用 MySQL：默认关闭，保证未配置时仍用本地 SQLite 运行。
# 填好凭据后，把这里改成 True，或运行前设环境变量 SR_DB_ENABLED=1。
MYSQL_ENABLED = os.environ.get("SR_DB_ENABLED", "1") == "1"
