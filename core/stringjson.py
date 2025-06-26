import json

def safe_json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)

def safe_json_loads(s: str):
    return json.loads(s)
