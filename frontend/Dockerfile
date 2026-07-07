# 1. Use an official lightweight Python runtime as a parent image
FROM python:3.11-slim

# 2. Set environment variables to prevent Python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Establish our secure working directory inside the container space
WORKDIR /app

# 4. Copy over requirements first to leverage Docker layer caching optimizations
COPY requirements.txt /app/

# 5. Install the required modules securely
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 6. Copy the rest of your application code (including your app.py) into the container
COPY . /app/

# 7. Inform Docker that the container listens on your specified target port
EXPOSE 3000

# 8. Configure Streamlit to run headlessly, bind to all interfaces, and target port 3000
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=3000", "--server.address=0.0.0.0", "--browser.gatherUsageStats=false"]