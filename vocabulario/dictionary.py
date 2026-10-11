import json
import unicodedata
from pathlib import Path


def normalize(text: str) -> str:
    # Keep ñ and accents: año/ano, si/sí, papa/papá have different meanings.
    return " ".join(unicodedata.normalize("NFC", text).casefold().strip().split())


def search_key(text: str) -> str:
    # Accent insensitive search only; never use this for grading.
    return "".join(
        c for c in unicodedata.normalize("NFD", normalize(text)) if unicodedata.category(c) != "Mn"
    )


class Dictionary:
    def __init__(self, path: Path):
        self.words = {}
        with path.open(encoding="utf-8") as f:
            for line in f:
                w = json.loads(line)
                self.words[w["id"]] = w
        self.ordered = sorted(self.words.values(), key=lambda w: w["rank"])

    def search(self, query="", topic=None, offset=0, limit=8):
        key = search_key(query)
        found = [
            w
            for w in self.ordered
            if (topic is None or topic in w["topics"])
            and (not key or key in search_key(w["word"] + " " + " ".join(w["meanings"])))
        ]
        if key:
            found.sort(key=lambda w: (search_key(w["word"]) != key, w["rank"]))
        return found[offset : offset + limit], len(found)

    def topics(self):
        counts = {}
        for w in self.ordered:
            for topic in w["topics"]:
                counts[topic] = counts.get(topic, 0) + 1
        return dict(sorted(counts.items()))
