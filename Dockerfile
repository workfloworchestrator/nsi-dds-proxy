# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183
#
# Build stage
FROM ghcr.io/astral-sh/uv:python3.13-alpine@sha256:f3f098c0532abb45dd01362a6ea3585b8b4cd53f0589be79efb8e529816637ce AS build
ARG VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DDS_PROXY=${VERSION}
WORKDIR /app
COPY pyproject.toml LICENSE README.md ./
COPY dds_proxy dds_proxy
RUN uv build --no-cache --wheel --out-dir dist

# Final stage
FROM ghcr.io/astral-sh/uv:python3.13-alpine@sha256:f3f098c0532abb45dd01362a6ea3585b8b4cd53f0589be79efb8e529816637ce
COPY --from=build /app/dist/*.whl /tmp/
RUN uv pip install --system --no-cache /tmp/*.whl && rm /tmp/*.whl
RUN addgroup -g 1000 dds_proxy && adduser -D -u 1000 -G dds_proxy dds_proxy
USER dds_proxy
WORKDIR /home/dds_proxy
EXPOSE 8000/tcp
CMD ["dds-proxy"]
