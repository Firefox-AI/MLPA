# Use a slim Python image for a smaller container
FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir uv
RUN uv pip install --system --editable .

# Copy the rest of the application code
COPY . .

# Install the package in editable mode to make the `mlpa` executable available
RUN pip install --no-cache-dir -e .

# Expose the application port
EXPOSE 8080

# Run the mlpa command using its full path inside the container
CMD ["/usr/local/bin/mlpa"]
