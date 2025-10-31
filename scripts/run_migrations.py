"""Run database migrations on startup."""

import logging

from db import init_db


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.info("Applying database migrations...")
    init_db()
    logging.info("Database migrations completed")


if __name__ == "__main__":
    main()

