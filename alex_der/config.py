import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict

CONFIG_DIR = Path.home() / ".alex-der"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_DIR = CONFIG_DIR / "sessions"


@dataclass
class Config:
    model: str = "qwen3-coder-next:cloud"
    ollama_host: str = "http://localhost:11434"
    working_dir: str = field(default_factory=os.getcwd)
    max_tokens: int = 8192
    temperature: float = 0.1
    auto_approve_reads: bool = True
    auto_approve_writes: bool = False
    auto_approve_bash: bool = False
    theme: str = "monokai"

    @classmethod
    def load(cls) -> "Config":
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**valid)
        return cls()

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(asdict(self), f, indent=2)

    def set(self, key: str, value):
        if not hasattr(self, key):
            raise KeyError(f"Unknown config key: {key}")
        field_type = type(getattr(self, key))
        setattr(self, key, field_type(value))
        self.save()
