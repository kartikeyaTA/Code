# Use an official lightweight Python image
FROM python:3.11-slim

# Set the working directory for frontend assets
WORKDIR /usr/src/app

# Copy all static frontend files into the container
COPY . .

# Expose the port the static server runs on
EXPOSE 5500

# Mimic your local command to serve the static files
CMD ["python", "-m", "http.server", "5500"]