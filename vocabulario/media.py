import asyncio
import hashlib
from pathlib import Path

import edge_tts


class Audio:
    def __init__(self, voice, directory=Path("runtime/audio")):
        self.voice = voice
        self.directory = directory

    async def get(self, text):
        key = hashlib.sha256((self.voice + ":" + text).encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (key + ".mp3")
        if not path.exists():
            temporary = path.with_suffix(".tmp.mp3")
            try:
                await asyncio.wait_for(
                    edge_tts.Communicate(text, self.voice).save(str(temporary)), timeout=25
                )
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return path
