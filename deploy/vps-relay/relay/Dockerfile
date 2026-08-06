FROM python:3.12-slim

WORKDIR /app
# Prefer Tsinghua mirror (CN VPS often times out on files.pythonhosted.org)
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "websockets>=12"

COPY server.py /app/server.py

ENV RELAY_HOST=0.0.0.0
ENV RELAY_PORT=8080
EXPOSE 8080

CMD ["python", "-u", "server.py"]
