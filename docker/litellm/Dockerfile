FROM ghcr.io/berriai/litellm:main-stable

# Bake config into the image so deployments are just "pull + restart".
COPY docker/litellm/litellm-config.yaml /app/config.yaml

