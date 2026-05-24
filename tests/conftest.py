import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Minimal stubs for optional cloud SDK imports used at module import time.
google = types.ModuleType("google")
cloud = types.ModuleType("google.cloud")
firestore = types.ModuleType("google.cloud.firestore")
storage = types.ModuleType("google.cloud.storage")
genai = types.ModuleType("google.genai")
genai_types = types.ModuleType("google.genai.types")

class _DummyClient:
    def __init__(self, *args, **kwargs):
        pass

class _DummyStorageClient:
    def __init__(self, *args, **kwargs):
        pass

    def bucket(self, *_args, **_kwargs):
        return object()

firestore.Client = _DummyClient
firestore.ArrayUnion = lambda items: items
storage.Client = _DummyStorageClient
genai.Client = _DummyClient
genai_types.GenerateContentConfig = object

sys.modules.setdefault("google", google)
sys.modules.setdefault("google.cloud", cloud)
sys.modules.setdefault("google.cloud.firestore", firestore)
sys.modules.setdefault("google.cloud.storage", storage)
sys.modules.setdefault("google.genai", genai)
sys.modules.setdefault("google.genai.types", genai_types)

openai = types.ModuleType("openai")
openai.OpenAI = _DummyClient
sys.modules.setdefault("openai", openai)
