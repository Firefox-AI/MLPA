# !/bin/bash

docker exec litellm_postgres psql -U litellm -c "CREATE DATABASE app_attest;"
