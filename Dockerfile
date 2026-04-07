FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Create persistent data directory
RUN mkdir -p /data

# DB and cache stored in /data (mount a volume here in production)
ENV GPS_DB_PATH=/data/gps_history.db
ENV GEOCODING_CACHE_PATH=/data/geocoding_cache.json
ENV VEHICLE_STATE_PATH=/data/vehicle_state.json

EXPOSE 8000

CMD ["python", "-m", "server.app"]
