# يعمل محلياً وعلى Railway. على Railway يُحقن PORT تلقائياً ويقرؤه app/main.py.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV APP_HOST=0.0.0.0 APP_PORT=8000
EXPOSE 8000
CMD ["python", "-m", "app.main"]
