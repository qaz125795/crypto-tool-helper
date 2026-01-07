FROM python:3.11-slim

WORKDIR /app

# 複製依賴文件
COPY requirements.txt .

# 安裝依賴
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用文件
COPY . .

# 設置環境變量
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# 暴露端口
EXPOSE 8080

# 使用 gunicorn 啟動（生產環境）
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "1", "--timeout", "120", "app:app"]
