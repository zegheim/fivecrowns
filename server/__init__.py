import json
import logging.config
from pathlib import Path

with open(Path(__file__).parent / "config/logging.json") as config:
    logging.config.dictConfig(json.load(config))
