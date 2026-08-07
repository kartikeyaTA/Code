############################
# Stage 1 - Build React App
############################
FROM node:22-alpine AS frontend-builder

WORKDIR /app/frontend

COPY web_app/package*.json ./

RUN npm ci

COPY web_app/ .

RUN npm run build

############################
# Stage 2 - Python Builder
############################
FROM python:3.11-slim AS backend-builder

WORKDIR /app/backend

RUN pip install poetry

COPY backend_for_frontend/pyproject.toml backend_for_frontend/poetry.lock ./

RUN poetry config virtualenvs.create false \
    && poetry install --no-root

COPY backend_for_frontend/ .

############################
# Stage 3 - Runtime
############################
FROM python:3.11-slim

WORKDIR /app

COPY --from=backend-builder /usr/local /usr/local

COPY --from=backend-builder /app/backend ./backend

COPY --from=frontend-builder /app/frontend/dist ./web_app/dist

EXPOSE 5500

WORKDIR /app/backend

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5500"]
