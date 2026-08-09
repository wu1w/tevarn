# Tevarn 后端镜像（Docker/compose 部署路径）
# 容器即隔离边界：single_user_mode 必须关（compose 默认注入 false），命令执行由容器边界兜底。
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY backend/requirements-prod.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend

# 数据（sqlite / secrets.json）落在 /app/data，挂 volume 持久化
ENV TEVARN_APP_HOST=0.0.0.0 \
    TEVARN_APP_PORT=8000 \
    TEVARN_DB_URL=sqlite+aiosqlite:////app/data/tevarn.db \
    TEVARN_SECRETS_FILE=/app/data/secrets.json

RUN mkdir -p /app/data \
    && useradd -r -u 10001 tevarn \
    && chown -R tevarn:tevarn /app/data

USER tevarn
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
