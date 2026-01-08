#!/bin/bash
# PlexCache-R Docker Build Script
#
# Usage:
#   ./docker/build.sh              # Build with default tag
#   ./docker/build.sh v3.0.0       # Build with specific version tag
#   ./docker/build.sh latest dev   # Build with multiple tags

set -e

# Configuration
IMAGE_NAME="brandonhaney/plexcache-r"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default tag
VERSION="${1:-latest}"

# Build from project root with docker/Dockerfile
echo "Building PlexCache-R Docker image..."
echo "  Image: ${IMAGE_NAME}:${VERSION}"
echo "  Context: ${PROJECT_ROOT}"
echo ""

cd "$PROJECT_ROOT"

# Build the image
docker build \
    -f docker/Dockerfile \
    -t "${IMAGE_NAME}:${VERSION}" \
    .

# Add additional tags if provided
shift || true
for tag in "$@"; do
    echo "Adding tag: ${IMAGE_NAME}:${tag}"
    docker tag "${IMAGE_NAME}:${VERSION}" "${IMAGE_NAME}:${tag}"
done

echo ""
echo "Build complete!"
echo "  Image: ${IMAGE_NAME}:${VERSION}"
echo ""
echo "To run:"
echo "  docker run -d -p 5757:5757 ${IMAGE_NAME}:${VERSION}"
echo ""
echo "Or use docker-compose:"
echo "  cd docker && docker-compose up -d"
