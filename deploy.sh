#!/bin/bash

# RefreshES API Deployment Script
# Usage: ./deploy.sh [prod|production]

set -e

ENVIRONMENT=${1:-prod}

echo "🚀 Deploying RefreshES API in $ENVIRONMENT mode..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Create logs directory if it doesn't exist
mkdir -p logs

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from env.example..."
    cp env.example .env
    echo "⚠️  Please update .env file with your configuration before running the application."
fi

case $ENVIRONMENT in
    "prod"|"production")
        echo "🏭 Starting production environment..."
        docker compose -f docker-compose.yml up --build -d
        echo "✅ Production environment started successfully!"
        echo "📊 API available at: http://localhost:8000"
        echo "📚 API docs available at: http://localhost:8000/docs"
        ;;
    *)
        echo "❌ Invalid environment. Use 'prod' or 'production'"
        exit 1
        ;;
esac

echo ""
echo "📋 Useful commands:"
echo "  View logs: docker compose logs -f"
echo "  Stop services: docker compose down"
echo "  Restart services: docker compose restart"
echo "  Check status: docker compose ps"
