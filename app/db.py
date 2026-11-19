"""MongoDB access. Collections:

- sources:  registered data sources (type, config, schedule)
- raw:      every ingested payload, untouched (data-lake style, for lineage/replay)
- records:  normalised supply-chain records in the canonical schema
- mappings: AI-learned column mappings, cached per source "fingerprint"
"""
from functools import lru_cache

from pymongo import ASCENDING, MongoClient

from . import config


@lru_cache
def get_db():
    if config.MONGO_URI.startswith("mongomock://"):
        import mongomock

        client = mongomock.MongoClient()
    else:
        client = MongoClient(config.MONGO_URI, tz_aware=True)
    db = client[config.MONGO_DB]
    db.records.create_index([("record_type", ASCENDING), ("sku", ASCENDING)])
    db.records.create_index([("source_id", ASCENDING)])
    db.records.create_index([("supplier", ASCENDING)])
    db.mappings.create_index([("fingerprint", ASCENDING)], unique=True)
    return db
