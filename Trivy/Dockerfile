############################
# Stage 1: Builder
############################
FROM python:3.12-alpine AS builder

WORKDIR /build

COPY requirements.txt .

# Install dependencies into isolated directory
RUN pip install --no-cache-dir --target=/install -r requirements.txt

# Remove build-tooling artifacts from the install directory
RUN rm -rf /install/pip* \
           /install/setuptools* \
           /install/wheel* \
           /install/easy_install*

############################
# Stage 2: Minimal Production Runtime
############################
FROM alpine:3.20

WORKDIR /app

# Install pure Python runtime without pip or setuptools
RUN apk add --no-cache python3

# Copy clean site-packages from builder
COPY --from=builder /install /usr/lib/python3.12/site-packages
COPY main.py .

EXPOSE 443

CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "443"]
