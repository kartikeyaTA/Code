# Stage 1: Compile dependencies inside a virtual environment
FROM python:3.11-slim AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Build the clean production runner image
FROM python:3.11-slim AS runner
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY main.py .

EXPOSE 3000
ENV PORT=3000
ENV PYTHONUNBUFFERED=1

# Execute uvicorn mapping directly to port 3000 to match pipeline expectations
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]