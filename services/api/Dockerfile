FROM python:3.13-slim
WORKDIR /app
COPY services/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/api /app/services/api
ENV PYTHONPATH=/app/services/api
CMD ["sh", "-c", "alembic -c services/api/alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
