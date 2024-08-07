ARG TARGETPLATFORM
ARG BUILDPLATFORM

# Normalize the platform by replacing slashes
ARG NORMALIZED_PLATFORM=${TARGETPLATFORM:-${BUILDPLATFORM}//\//-}

FROM alpine:3.19.3@sha256:8d733e27df31ac40ec64633002a200a0aed5477866730e0bfeb8d2dec5d8e76a AS base-linux-amd64
FROM alpine:3.19.3@sha256:6f8cacb1dbb6ea4606dfaa23b6b8b1692a4e63cc9c2f91b943cff7deccab8792 AS base-linux-arm64


FROM base-${NORMALIZED_TARGETPLATFORM:-linux-amd64} AS base

# Pass information about the build to the container
ARG DOCKER_METADATA_OUTPUT_JSON='{}'
ENV DOCKER_METADATA_OUTPUT_JSON=${DOCKER_METADATA_OUTPUT_JSON}

RUN apk add py-pip curl

RUN pip install fastapi "uvicorn[standard]" jwt requests pygithub "sentry-sdk[fastapi]" --break-system-packages

COPY ./src /app

WORKDIR /app

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s CMD curl --fail http://localhost:8000/health || exit 1

ENTRYPOINT [ "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000" ]