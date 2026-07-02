# Use an official lightweight Python image
FROM python:3.11-slim

# Set environment variables to optimize Python behavior in Docker
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container
WORKDIR /app

# Copy only requirements first to leverage Docker's caching mechanism
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port Uvicorn will run on
EXPOSE 80

# Run the application (bind to 0.0.0.0 so it's accessible outside the container)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]