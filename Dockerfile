FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md constraints-py311-linux.txt ./
COPY src ./src

RUN pip install --no-cache-dir -c constraints-py311-linux.txt -e .

EXPOSE 8000

CMD ["uvicorn", "quant_trading_platform.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
