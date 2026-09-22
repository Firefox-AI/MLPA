# Use a slim Python image for a smaller container
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y postgresql-client libjemalloc2 && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app
COPY . .

# Create directory for QA certificates with appropriate permissions
RUN mkdir -p /tmp/qa_certificates && chmod 777 /tmp/qa_certificates

# App Attest database migration script (deploy Job)
RUN chmod +x /app/scripts/migrate-app-attest-database.sh
RUN chmod +x /app/scripts/migrate-litellm-database.sh

# App Attest rollback script (manual, incident response - AIPLAT-1189)
RUN chmod +x /app/scripts/rollback-app-attest-database.sh

# Install dependencies
RUN pip install --no-cache-dir uv==0.10.8
RUN apt-get update && apt-get install -y git
RUN uv pip install --system --editable .

# Expose the application port
EXPOSE 8080

RUN chmod +x /app/docker-entrypoint.sh

CMD ["/app/docker-entrypoint.sh"]
