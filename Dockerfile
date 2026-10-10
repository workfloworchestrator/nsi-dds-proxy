# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
#
# Build stage
FROM ghcr.io/astral-sh/uv:python3.13-alpine@sha256:266908266118b7de9d6ece62b11f5a83917a19c13e711379dfaa74d4994bc1ff AS build
ARG VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DDS_PROXY=${VERSION}
WORKDIR /app
COPY pyproject.toml LICENSE README.md ./
COPY dds_proxy dds_proxy
RUN uv build --no-cache --wheel --out-dir dist

# Final stage
FROM ghcr.io/astral-sh/uv:python3.13-alpine@sha256:266908266118b7de9d6ece62b11f5a83917a19c13e711379dfaa74d4994bc1ff
COPY --from=build /app/dist/*.whl /tmp/
RUN uv pip install --system --no-cache /tmp/*.whl && rm /tmp/*.whl
RUN addgroup -g 1000 dds_proxy && adduser -D -u 1000 -G dds_proxy dds_proxy
USER dds_proxy
WORKDIR /home/dds_proxy
EXPOSE 8000/tcp
CMD ["dds-proxy"]
