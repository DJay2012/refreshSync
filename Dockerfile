# Production Dockerfile for RefreshES API
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    PYTHONHASHSEED=random \
    PYTHONOPTIMIZE=1

# Set work directory
WORKDIR /app

# Install system dependencies including libraries for Charts API
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libopenjp2-7-dev \
    libtiff5-dev \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy production requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Download required NLTK datasets (offline-friendly layer)
RUN python - <<'PY'
import nltk
packages = [
    "punkt",
    "wordnet",
    "averaged_perceptron_tagger",
    "stopwords"
]
for pkg in packages:
    try:
        nltk.download(pkg)
        print(f"Downloaded NLTK package: {pkg}")
    except Exception as e:
        print(f"nltk download {pkg} failed: {e}")
PY

# Copy application code
COPY app/ ./app/
COPY main.py ./main.py
COPY backtracking_worker.py ./backtracking_worker.py
COPY esBacktracking/ ./esBacktracking/
COPY config/ ./config/
COPY esBooleanTranslator/ ./esBooleanTranslator/
COPY allSearchAPI/ ./allSearchAPI/
# Include elasticTaggingAPI for tagging endpoints
COPY elasticTaggingAPI/ ./elasticTaggingAPI/

# Create logs directory with proper permissions
RUN mkdir -p logs && \
    chmod 755 logs

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash --uid 1000 app && \
    chown -R app:app /app
USER app

# Expose port
EXPOSE $PORT

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:$PORT/health || exit 1

# Run API and Backtracking Worker in the same container without external scripts
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 & python backtracking_worker.py"]


