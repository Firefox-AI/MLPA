"""
Simulate projected load

--- Run this in DB to fill DB with 1M users ---
DELETE FROM "LiteLLM_EndUserTable";
WITH RECURSIVE generate_series(id) AS (
    SELECT 0
    UNION ALL
    SELECT id + 1 FROM generate_series WHERE id < 1000000
)
INSERT INTO "LiteLLM_EndUserTable" ("user_id")
SELECT
    'test-user-' || id AS "user_id"
FROM generate_series;
SELECT * FROM public."LiteLLM_EndUserTable"
"""

import asyncio
import json
import os
import random
import time
import uuid

import httpx
import jwt
import tqdm
from dotenv import load_dotenv
from tabulate import tabulate

load_dotenv()

# Projected total load
USERS = 2_500_000
REQ_PER_MINUTE = 465
# REQ_PER_SECOND = REQ_PER_MINUTE / 60
REQ_PER_SECOND = 50

PROXY_API_BASE = "http://localhost:8080"
JWT_SECRET = os.getenv("JWT_SECRET")


class User:
	def __init__(self, id: str = None):
		self.id = id or str(uuid.uuid4())
		self.stats = {}
		self.key = jwt.encode({"user_id": self.id}, JWT_SECRET, algorithm="HS256")

	async def simulate_request(self):
		start = time.time()
		payload = {
			"user_id": self.id,
			"fxa_payload": {"uid": "test"},
			"messages": [{"role": "user", "content": "Hello!"}],
		}
		headers = {
			"Authorization": "Bearer ",  # fill in with gcloud auth print-identity-token
			"Content-Type": "application/json",
			"proxy-auth": f"Bearer {self.key}",
		}
		async with httpx.AsyncClient() as client:
			try:
				url = f"{PROXY_API_BASE}/v1/chat/completions"

				response = await client.post(url, json=payload, headers=headers)
			except Exception as e:
				self.stats = {"success": False, "error": e}
				return
			response.raise_for_status()
			end = time.time()
			duration = end - start
			self.stats = {"success": True, "duration": duration}

	def __str__(self):
		return f"User(id={self.id}, stats={self.stats})"


async def test_server_rps_limit(max_rps=8, test_duration=10):
	"""
	Test the maximum requests per second (RPS) the server can handle.
	Args:
		max_rps (int): Maximum RPS to test.
		test_duration (int): Duration of the test in seconds.
	"""
	users = [User(f"test-user-{i}") for i in range(USERS)]
	random.shuffle(users)
	start_time = time.time()
	tasks = []
	with tqdm.tqdm(
		total=int(max_rps * test_duration), desc="RPS Test", unit="req"
	) as pbar:
		for i in range(int(max_rps * test_duration)):
			tasks.append(asyncio.create_task(users[i].simulate_request()))
			await asyncio.sleep(1 / max_rps)
			pbar.update(1)
			if time.time() - start_time > test_duration:
				break

	await asyncio.gather(*tasks)

	durations = [user.stats.get("duration") for user in users if user.stats]
	success = [user.stats.get("success") for user in users if user.stats]
	failures = sum(1 for s in success if s is False)
	print(f"Tested RPS: {max_rps} --- Actual RPS: {pbar.n / test_duration:.2f}")
	print(f"Total requests: {pbar.n}")
	print(f"Successful requests: {sum(success)}")
	print(f"Failed requests: {failures}")
	if durations:
		print(
			f"Average request duration: {sum(durations) / len(durations):.4f} seconds"
		)


def calculate_metric_stats():
	with open("metrics.jsonl", "r") as f:
		data = [json.loads(line) for line in f.readlines()]

	metrics = [
		"app_attest_verification",
		"get_user",
		"create_user",
		"completion",
		"total",
	]
	averages = {}
	counts = {m: 0 for m in metrics}
	sums = {m: 0.0 for m in metrics}

	for entry in data:
		for metric in metrics:
			if metric in entry:
				value = entry[metric]
				if isinstance(value, list):
					sums[metric] += sum(value)
					counts[metric] += len(value)
				else:
					sums[metric] += value
					counts[metric] += 1

	for metric in metrics:
		if counts[metric]:
			averages[metric] = sums[metric] / counts[metric]
		else:
			averages[metric] = None

	headers = ["Metric", "Average"]
	table = [
		[metric, f"{averages[metric]:.4f}" if averages[metric] is not None else "N/A"]
		for metric in metrics
	]
	print(tabulate(table, headers=headers, tablefmt="grid"))


if __name__ == "__main__":
	asyncio.run(test_server_rps_limit(5, 20))
	calculate_metric_stats()
