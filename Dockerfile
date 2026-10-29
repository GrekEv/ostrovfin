FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY intelligence ./intelligence
COPY sql ./sql
COPY data ./data
COPY web ./web
EXPOSE 8090
CMD ["uvicorn", "intelligence.api:app", "--host", "0.0.0.0", "--port", "8090"]
