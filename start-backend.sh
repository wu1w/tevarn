#!/bin/bash
# Takton 后端启动脚本（开发环境）

cd "E:/项目/takton/backend"

# 设置环境变量
export PYTHONPATH="E:\\项目\\takton"
export JWT_SECRET="takton-dev-secret-key-2026"
export API_KEY="takton-dev-api-key-2026"

# 启动服务
"C:/Users/developer/AppData/Local/Programs/Python/Python314/python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
