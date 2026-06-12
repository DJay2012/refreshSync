#!/bin/bash

# RefreshES API Deployment Script
# This script systematically deploys the RefreshES API application using Docker Compose

set -e

echo "🚀 Starting RefreshES API Deployment..."

# Ask for port
read -p "Enter port to expose (default 8000): " INPUT_PORT
INPUT_PORT=${INPUT_PORT:-8000}
echo "Using port: $INPUT_PORT"

# Write API_PORT to .env
if [ -f .env ]; then
    sed -i '/^API_PORT=/d' .env
fi
echo "API_PORT=$INPUT_PORT" >> .env
echo ""

# Check if Docker is running
echo "🔍 Checking Docker status..."
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi
echo "✅ Docker is running"

# Check if Docker Compose is available
echo "🔍 Checking Docker Compose..."
if command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
    echo "✅ Docker Compose (v1) is available"
elif docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
    echo "✅ Docker Compose (v2) is available"
else
    echo "❌ Docker Compose is not installed. Please install Docker Compose and try again."
    exit 1
fi

# Create logs directory if it doesn't exist
echo "📁 Creating logs directory..."
mkdir -p logs
# On Windows, permissions are handled by Docker Desktop
# On Linux/Mac, try to set permissions (will be fixed in container startup if needed)
if [ "$(uname)" != "MINGW"* ] && [ "$(uname)" != "MSYS"* ] && [ "$(uname)" != "CYGWIN"* ]; then
    chmod 777 logs 2>/dev/null || true
fi

# Check for .env file
if [ ! -f .env ]; then
    echo "⚠  Warning: .env file not found!"
    echo "Creating .env from env.example..."
    if [ -f env.example ]; then
        cp env.example .env
        echo "✅ Created .env file. Please update it with your configuration."
        echo "Press Enter to continue after updating .env, or Ctrl+C to cancel..."
        read
    else
        echo "❌ env.example not found. Please create a .env file with your configuration."
        exit 1
    fi
fi

# Check for existing containers and stop them systematically
echo "🔍 Checking for existing RefreshES API containers..."

# Check and stop individual containers that might conflict
echo "🛑 Checking for conflicting containers on port $INPUT_PORT..."
for container in $(docker ps -q --filter "publish=$INPUT_PORT" 2>/dev/null); do
    echo "  Stopping container $container on port $INPUT_PORT..."
    docker stop $container >/dev/null 2>&1 || true
    docker rm $container >/dev/null 2>&1 || true
done

# Check and stop individual containers that might conflict on Redis port
echo "🛑 Checking for conflicting containers on port 6379..."
for container in $(docker ps -q --filter "publish=6379" 2>/dev/null); do
    echo "  Stopping container $container on port 6379..."
    docker stop $container >/dev/null 2>&1 || true
    docker rm $container >/dev/null 2>&1 || true
done

# Stop any existing RefreshES API containers
echo "🛑 Stopping existing RefreshES API containers..."
$COMPOSE_CMD down >/dev/null 2>&1 || true

# Remove any orphaned containers with RefreshES API names
echo "🧹 Cleaning up orphaned containers..."
docker rm refresh-es-api-prod >/dev/null 2>&1 || true
docker rm refresh-es-redis-prod >/dev/null 2>&1 || true

# Pull latest images from Docker Hub
echo "📥 Pulling latest images from Docker Hub..."
if ! $COMPOSE_CMD pull; then
    echo "❌ Failed to pull images. Check your internet connection and Docker Hub access."
    exit 1
fi
echo "✅ Images pulled successfully"

# Start the services
echo "🏗  Starting RefreshES API services..."
if ! $COMPOSE_CMD up -d; then
    echo "❌ Failed to start services. Check the logs above."
    exit 1
fi

# Wait for services to be healthy
echo "⏳ Waiting for services to start..."
sleep 10

# Check service status
echo "📊 Checking service status..."
$COMPOSE_CMD ps

# Verify all services are running
echo "🔍 Verifying deployment..."
if $COMPOSE_CMD ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" | grep -q "Up"; then
    echo "✅ All services are running successfully!"
else
    echo "⚠  Some services may not be running properly. Check logs with: $COMPOSE_CMD logs"
fi

# Display access information
echo ""
echo "✅ RefreshES API deployment completed successfully!"
echo ""
echo "🌐 Access URLs:"
echo "   API: http://localhost:$INPUT_PORT"
echo "   API Docs: http://localhost:$INPUT_PORT/docs"
echo "   Health Check: http://localhost:$INPUT_PORT/health"
echo "   Redis: localhost:6379"
echo ""
echo "📋 Useful commands:"
echo "   View logs: $COMPOSE_CMD logs -f"
echo "   View API logs: $COMPOSE_CMD logs -f refresh-es-api"
echo "   View Redis logs: $COMPOSE_CMD logs -f redis"
echo "   Stop services: $COMPOSE_CMD down"
echo "   Restart services: $COMPOSE_CMD restart"
echo "   Update services: $COMPOSE_CMD pull && $COMPOSE_CMD up -d"