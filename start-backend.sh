#!/bin/bash
# Tevarn 后端启动脚本（开发环境）

cd "E:/项目/tevarn/backend"

# 设置环境变量
export PYTHONPATH="E:\\项目\\tevarn"
export JWT_SECRET="tevarn-dev-secret-key-2026"
export API_KEY="tevarn-dev-api-key-2026"

# 启动服务
"C:/Users/developer/AppData/Local/Programs/Python/Python314/python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
