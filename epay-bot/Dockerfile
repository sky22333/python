FROM python:3.12-alpine

WORKDIR /app

COPY requirements.txt .
RUN apk add --no-cache curl ca-certificates && \
    update-ca-certificates && \
    pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "bot.py"]