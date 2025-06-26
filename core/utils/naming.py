def toCamel(string: str) -> str:
    parts = string.split("_")
    return parts[0].lower() + "".join(word.capitalize() for word in parts[1:])
