FROM python:3.12-slim
WORKDIR /app
COPY binance_451_connectivity_probe.py /app/probe.py
CMD ["python", "-u", "/app/probe.py"]
