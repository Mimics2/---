FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Создаем папку для данных если нужно
RUN mkdir -p /data

CMD ["python", "main.py"]
