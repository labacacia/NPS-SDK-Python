FROM python:3.12-slim AS runtime

WORKDIR /app

RUN addgroup --gid 1001 nipca \
 && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" nipca \
 && mkdir -p /data && chown nipca:nipca /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

USER nipca

EXPOSE 17440

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:17440/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "17440"]
