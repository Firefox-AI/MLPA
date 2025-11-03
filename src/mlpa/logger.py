import logging
import sys

from loguru import logger as loguru_logger

# Remove existing handlers
for handler in logging.root.handlers[:]:
	logging.root.removeHandler(handler)


class InterceptHandler(logging.Handler):
	def emit(self, record):
		# Get corresponding Loguru level
		try:
			level = loguru_logger.level(record.levelname).name
		except ValueError:
			level = record.levelno

		# Find caller to get correct stack depth
		frame, depth = logging.currentframe(), 2
		while frame.f_back and frame.f_code.co_filename == logging.__file__:
			frame = frame.f_back
			depth += 1

		loguru_logger.opt(depth=depth, exception=record.exc_info).log(
			level, record.getMessage()
		)


def setup_logger(env):
	logging.basicConfig(
		handlers=[InterceptHandler()],
		level=getattr(logging, env.LOG_LEVEL.upper(), logging.INFO),
	)
	loguru_logger.add(
		env.LOG_FILE,
		rotation=env.LOG_ROTATION,
		compression=env.LOG_COMPRESSION,
		level=env.LOG_LEVEL,
		backtrace=True,
		diagnose=True,
	)
