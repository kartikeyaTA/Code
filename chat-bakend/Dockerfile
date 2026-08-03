FROM python:3.10-slim

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade \
    "pip>=26.1.2" \
    "setuptools>=83.0.0" \
    "wheel>=0.46.2"

# 1. Copy ONLY the requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copy the application code last (changes frequently)
COPY main.py .

EXPOSE 8000


CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
