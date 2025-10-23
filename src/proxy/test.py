"""
Generate 1M users using any-llm-gateway API

This script creates 1 million users using the any-llm-gateway API
with async batching for efficient user creation.
"""

import asyncio
import json
import os
import random
import time
import uuid
from typing import List, Tuple

import httpx
import jwt
import tqdm
from dotenv import load_dotenv
from tabulate import tabulate

load_dotenv()

TOTAL_USERS = 1_000_000
BATCH_SIZE = 10  # Number of users to create in parallel
PROXY_API_BASE = os.getenv("GATEWAY_API_BASE", "http://localhost:8000")
GATEWAY_MASTER_KEY = os.getenv("GATEWAY_MASTER_KEY")
USERS = 2_500_000
REQ_PER_MINUTE = 465
REQ_PER_SECOND = 50
JWT_SECRET = os.getenv("JWT_SECRET")


async def create_user_batch(
	user_ids: List[str], client: httpx.AsyncClient
) -> Tuple[int, int]:
	"""
	Create a batch of users using the any-llm-gateway API.

	Args:
	    user_ids: List of user IDs to create
	    client: HTTP client for making requests

	Returns:
	    Tuple of (successful_creations, failed_creations)
	"""
	if not GATEWAY_MASTER_KEY:
		raise ValueError("GATEWAY_MASTER_KEY environment variable is required")

	headers = {
		"X-AnyLLM-Key": f"Bearer {GATEWAY_MASTER_KEY}",
		"Content-Type": "application/json",
	}

	success_count = 0
	failed_count = 0

	tasks = []
	for user_id in user_ids:
		payload = {
			"user_id": user_id,
			"alias": f"Test User {user_id}",
			"blocked": False,
			"metadata": {"created_by": "bulk_script", "batch_id": str(uuid.uuid4())},
		}

		task = client.post(f"{PROXY_API_BASE}/v1/users", json=payload, headers=headers)
		tasks.append(task)

	# Wait for all requests in the batch to complete
	responses = await asyncio.gather(*tasks, return_exceptions=True)

	for response in responses:
		if isinstance(response, Exception):
			failed_count += 1
		elif hasattr(response, "status_code"):
			if response.status_code == 201:
				success_count += 1
			elif response.status_code == 409:
				# User already exists, count as success
				success_count += 1
			else:
				failed_count += 1
		else:
			failed_count += 1

	return success_count, failed_count


async def generate_users():
	"""
	Generate 1M users using the any-llm-gateway API with async batching.
	"""
	if not GATEWAY_MASTER_KEY:
		print("Error: GATEWAY_MASTER_KEY environment variable is required")
		return

	print(f"Starting to create {TOTAL_USERS:,} users...")
	print(f"Gateway URL: {PROXY_API_BASE}")
	print(f"Batch size: {BATCH_SIZE}")
	print()

	start_time = time.time()
	total_success = 0
	total_failed = 0

	async with httpx.AsyncClient(timeout=30.0) as client:
		with tqdm.tqdm(total=TOTAL_USERS, desc="Creating users", unit="users") as pbar:
			for batch_start in range(0, TOTAL_USERS, BATCH_SIZE):
				batch_end = min(batch_start + BATCH_SIZE, TOTAL_USERS)
				user_ids = [f"test-user-{i}" for i in range(batch_start, batch_end)]

				try:
					success, failed = await create_user_batch(user_ids, client)
					total_success += success
					total_failed += failed

					pbar.update(len(user_ids))
					pbar.set_postfix(
						{
							"Success": f"{total_success:,}",
							"Failed": f"{total_failed:,}",
							"Rate": f"{total_success / (time.time() - start_time):.1f}/s",
						}
					)

				except Exception as e:
					print(f"Error in batch {batch_start}-{batch_end}: {e}")
					total_failed += len(user_ids)
					pbar.update(len(user_ids))

	end_time = time.time()
	duration = end_time - start_time

	print(f"\nUser creation completed!")
	print(f"Total users created: {total_success:,}")
	print(f"Total failures: {total_failed:,}")
	print(f"Duration: {duration:.2f} seconds")
	print(f"Average rate: {total_success / duration:.1f} users/second")
	print(
		f"Success rate: {(total_success / (total_success + total_failed)) * 100:.1f}%"
	)


class User:
	def __init__(self, user_id: str = None):
		self.id = user_id or str(uuid.uuid4())
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
	with open("metrics.jsonl", "r", encoding="utf-8") as f:
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
	import sys

	if len(sys.argv) > 1 and sys.argv[1] == "generate-users":
		# Generate 1M users using any-llm-gateway API
		asyncio.run(generate_users())
	elif len(sys.argv) > 1 and sys.argv[1] == "test-rps":
		# Run the original RPS test
		asyncio.run(test_server_rps_limit(5, 20))
		calculate_metric_stats()
	else:
		print("Usage:")
		print(
			"  python test.py generate-users  # Generate 1M users via any-llm-gateway API"
		)
		print("  python test.py test-rps        # Run RPS test")
		print()
		print("Environment variables required for generate-users:")
		print("  GATEWAY_MASTER_KEY - Master key for any-llm-gateway")
		print("  PROXY_API_BASE  - Gateway URL (default: http://localhost:8000)")
