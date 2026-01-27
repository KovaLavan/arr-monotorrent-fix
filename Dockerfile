FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV UPSTREAM="http://192.168.1.235:9696"
ENV TIMEOUT="30"

EXPOSE 8787
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8787"]
