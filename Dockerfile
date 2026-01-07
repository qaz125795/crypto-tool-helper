FROM python:3.11-slim

WORKDIR /app

# 複製依賴文件
COPY requirements.txt .

# 安裝依賴
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用文件
COPY . .

# 暴露端口（Zeabur 會自動設置 PORT 環境變量）
EXPOSE 3000

# 啟動應用
CMD ["python", "app.py"]

