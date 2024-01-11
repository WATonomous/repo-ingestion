FROM alpine:3.19.0@sha256:13b7e62e8df80264dbb747995705a986aa530415763a6c58f84a3ca8af9a5bcd

RUN apk add py-pip

RUN pip install fastapi "uvicorn[standard]" jwt requests --break-system-packages

COPY ./src /app

WORKDIR /app

EXPOSE 8000

ENTRYPOINT [ "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000" ]