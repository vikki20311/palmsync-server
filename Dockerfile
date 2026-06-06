FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY server_wan.py .

CMD ["gunicorn", "server_wan:app", "--bind", "0.0.0.0:8080"]