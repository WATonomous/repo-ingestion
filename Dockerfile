FROM alpine:3.19.0@sha256:13b7e62e8df80264dbb747995705a986aa530415763a6c58f84a3ca8af9a5bcd

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