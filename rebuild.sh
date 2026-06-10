#!/bin/bash

# Rebuild script to update Docker container with latest code changes
# Usage: ./rebuild.sh [prod|production]

set -e

ENVIRONMENT=${1:-auto}

echo "🔨 Rebuilding RefreshES API Docker Container..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Auto-detect environment if not specified
if [ "$ENVIRONMENT" == "auto" ]; then
    echo "🔍 Detecting environment..."
    
    if docker compose -f docker-compose.yml ps 2>/dev/null | grep -q "Up"; then
        ENVIRONMENT="prod"
        COMPOSE_FILE="docker-compose.yml"
    else
        echo "⚠️  No running services detected. Defaulting to production mode..."
        ENVIRONMENT="prod"
        COMPOSE_FILE="docker-compose.yml"
    fi
    
    echo "📋 Detected environment: $ENVIRONMENT"
fi

# Set compose file based on environment
case $ENVIRONMENT in
    "prod"|"production")
        COMPOSE_FILE="docker-compose.yml"
        ENV_NAME="production"
        ;;
    *)
        echo "❌ Invalid environment. Use 'prod' or 'production'"
        exit 1
        ;;
esac

echo ""
echo "⚠️  This will stop the container, rebuild the image, and restart it."
echo "   Environment: $ENV_NAME"
echo "   Compose file: $COMPOSE_FILE"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 1
fi

echo ""
echo "🛑 Stopping existing services..."
docker compose -f $COMPOSE_FILE down

echo ""
echo "🔨 Rebuilding Docker image with latest code changes..."
echo "   (This may take a few minutes...)"
docker compose -f $COMPOSE_FILE build --no-cache

echo ""
echo "🚀 Starting services..."
docker compose -f $COMPOSE_FILE up -d

echo ""
echo "⏳ Waiting for services to be ready..."
sleep 10

# Check health
echo "🏥 Checking service health..."
MAX_RETRIES=6
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -f -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Service health check passed!"
        break
    else
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
            echo "⏳ Health check failed, retrying... ($RETRY_COUNT/$MAX_RETRIES)"
            sleep 5
        else
            echo "⚠️  Health check failed after $MAX_RETRIES attempts."
            echo "📋 Check logs with: docker compose -f $COMPOSE_FILE logs"
            exit 1
        fi
    fi
done

echo ""
echo "✅ Rebuild complete!"
echo ""
echo "📋 Verifying MongoDB timeout settings..."
echo "   Run: ./diagnose_mongo_timeout.sh"
echo ""
echo "📋 Service Information:"
echo "  📊 API: http://localhost:8000"
echo "  📚 API docs: http://localhost:8000/docs"
echo "  🏥 Health: http://localhost:8000/health"
echo ""
echo "   Check logs for: 'MongoDB connection established with timeouts: serverSelection=60000ms'"








