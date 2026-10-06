# Tallyhound in a container. Data stays on the machine that runs it.
#   docker compose up --build        (app + Ollama, see docker-compose.yml)
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home tallyhound && mkdir -p /data && chown tallyhound /data
USER tallyhound

ENV TALLYHOUND_STATE_DIR=/data \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
VOLUME ["/data"]
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.headless=true"]
