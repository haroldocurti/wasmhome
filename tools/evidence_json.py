"""Parse evidence JSON without ambiguous duplicate keys or non-finite numbers."""
import json


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(text):
    def invalid(value):
        raise ValueError(f"invalid JSON constant: {value}")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def read(path):
    return loads(path.read_text(encoding="utf-8-sig"))
