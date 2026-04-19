FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install stable-baselines3 gym numpy requests docker

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "dqn_agent.py"]
