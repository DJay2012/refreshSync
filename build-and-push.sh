#!/bin/bash

echo "========================================"
echo "RefreshES API - Build and Push Docker"
echo "========================================"
echo ""

# Check if Docker is running
docker info >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "ERROR: Docker is not running. Please start Docker and try again."
    exit 1
fi
echo "Docker is running."
echo ""

# ── Read / bump version ──────────────────────────────────────────────────────
if [ ! -f version.txt ]; then
    echo "1.0.0" > version.txt
fi

IMAGE_TAG=$(cat version.txt | tr -d '[:space:]')
# Login as personal account (org member with write access to pnqresearch).
# Override without editing: DOCKERHUB_USER=yourname ./build-and-push.sh
DOCKERHUB_LOGIN_USER="${DOCKERHUB_USER:-kumarpnq}"
DOCKERHUB_REPO="pnqresearch/refreshsync"

echo "Current version: $IMAGE_TAG"
echo ""
read -p "Increment version? (y/n): " increment
if [[ "$increment" == "y" ]]; then
    read -p "New version (current: $IMAGE_TAG): " NEW_VERSION
    if [ -z "$NEW_VERSION" ]; then
        echo "ERROR: Version cannot be empty!"
        exit 1
    fi
    echo -n "$NEW_VERSION" > version.txt
    IMAGE_TAG="$NEW_VERSION"
    echo "New version: $IMAGE_TAG"
else
    echo "Using current version: $IMAGE_TAG"
fi
echo ""

# ── Docker Hub login ─────────────────────────────────────────────────────────
echo "========================================"
echo "Step 1: Docker Hub Login (as $DOCKERHUB_LOGIN_USER)"
echo "========================================"
# Password/token can be supplied via DOCKERHUB_PASSWORD env var (CI), else prompt
if [ -n "$DOCKERHUB_PASSWORD" ]; then
    echo "$DOCKERHUB_PASSWORD" | docker login -u "$DOCKERHUB_LOGIN_USER" --password-stdin
else
    read -s -p "Docker Hub password/token for $DOCKERHUB_LOGIN_USER: " DOCKERHUB_PW
    echo ""
    echo "$DOCKERHUB_PW" | docker login -u "$DOCKERHUB_LOGIN_USER" --password-stdin
fi
if [ $? -ne 0 ]; then
    echo "Docker login failed!"
    exit 1
fi
echo ""

# ── Ensure a buildx builder with docker-container driver exists ──────────────
# The docker-container driver is required for cross-platform (linux/amd64) builds
# on Apple Silicon (arm64) Macs.
echo "========================================"
echo "Step 2: Setting up buildx builder"
echo "========================================"

BUILDER_NAME="pnq-multiarch"
if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
    echo "Creating buildx builder '$BUILDER_NAME'..."
    docker buildx create --name "$BUILDER_NAME" --driver docker-container --use
    docker buildx inspect --bootstrap
else
    echo "Using existing builder '$BUILDER_NAME'."
    docker buildx use "$BUILDER_NAME"
fi
echo ""

# ── Build for linux/amd64 and push directly ──────────────────────────────────
# buildx cross-compiled images cannot be loaded into the local Docker image
# store, so we pass --push to send them straight to the registry.
echo "========================================"
echo "Step 3: Build (linux/amd64) + Push"
echo "========================================"
echo "  Image: $DOCKERHUB_REPO"
echo "  Tags : $IMAGE_TAG  latest"
echo "  Arch : linux/amd64"
echo ""

docker buildx build \
    --platform linux/amd64 \
    --tag "$DOCKERHUB_REPO:$IMAGE_TAG" \
    --tag "$DOCKERHUB_REPO:latest" \
    --push \
    -f Dockerfile .

if [ $? -ne 0 ]; then
    echo ""
    echo "Build/push failed! Check the error messages above."
    exit 1
fi

echo ""
echo "========================================"
echo "Build and Push completed successfully!"
echo "========================================"
echo ""
echo "Repository : $DOCKERHUB_REPO"
echo "Tags pushed: $IMAGE_TAG  latest"
echo "Platform   : linux/amd64"
echo ""
echo "Pull with:"
echo "  docker pull $DOCKERHUB_REPO:$IMAGE_TAG"
echo ""
