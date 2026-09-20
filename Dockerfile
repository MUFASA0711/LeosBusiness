FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
# 1 Worker + Threads: das Login-Limit liegt im Arbeitsspeicher und soll für alle Anfragen gelten
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-8080} -w 1 --threads 4 app:app"]
