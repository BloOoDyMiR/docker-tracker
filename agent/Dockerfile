FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV AGENT_HOST=0.0.0.0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY access.py app.py .

EXPOSE 8001

# The Docker socket is owned by root on the host. This process is the only
# container that mounts it, and it is not published beyond loopback.
CMD ["python", "app.py"]
