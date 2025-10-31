# !/bin/bash

docker exec -it litellm_postgres psql -U litellm -c "CREATE DATABASE app_attest;"
