# Use a slim Python image for a smaller container
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app
COPY . .

# Create directory for QA certificates with appropriate permissions
RUN mkdir -p /tmp/qa_certificates && chmod 777 /tmp/qa_certificates

# Run appattest database migration script
RUN chmod +x /app/scripts/migrate-app-attest-database.sh

# Install dependencies
RUN pip install --no-cache-dir uv
RUN apt-get update && apt-get install -y git
RUN uv pip install --system --editable .

# Expose the application port
EXPOSE 8080

# Run the mlpa command using its full path inside the container
CMD ["/usr/local/bin/mlpa"]
