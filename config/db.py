from pymongo import MongoClient
from config.settings import settings
from urllib.parse import quote_plus
import re

def _build_safe_uri(uri: str) -> str:
    match = re.match(r"(mongodb(?:\+srv)?://)([^:]+):([^@]+)@(.+)", uri)
    if match:
        scheme, user, password, rest = match.groups()
        return f"{scheme}{quote_plus(user)}:{quote_plus(password)}@{rest}"
    return uri

client = MongoClient(_build_safe_uri(settings.MONGO_URI))
db = client[settings.DATABASE_NAME]
prediction_collection = db[settings.COLLECTION_NAME]