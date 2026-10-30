"""Acceptance-only API: record real query vectors, forbid generation calls."""
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"services/api"),str(ROOT)]
from app.embedding.factory import EmbeddingProviderFactory
from app.llm.factory import ProviderFactory as LLMProviderFactory
from app.main import app
import uvicorn

EVENTS=ROOT/".tools/stage4-task9-api-events.jsonl"
EVENTS.parent.mkdir(exist_ok=True)


def event(value):
    with EVENTS.open("a",encoding="utf-8") as stream:stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+"\n")


original_embedding=EmbeddingProviderFactory.create
def embedding(settings):
    provider=original_embedding(settings)
    original=provider.embed_texts
    async def observed(texts):
        identity=uuid4().hex
        event({"event":"embedding_started","id":identity,"text_sha256":[hashlib.sha256(t.encode()).hexdigest() for t in texts]})
        try:result=await original(texts)
        except Exception as exc:
            event({"event":"embedding_failed","id":identity,"error_type":type(exc).__name__});raise
        event({"event":"embedding_completed","id":identity,"model":provider.model_name,"vectors":result})
        return result
    provider.embed_texts=observed
    return provider


original_llm=LLMProviderFactory.create
class GenerationGuard:
    def __init__(self,provider):self.provider=provider
    def __getattr__(self,name):
        if name in {"complete","complete_structured","complete_with_tools","stream"}:
            def deny(*args,**kwargs):
                event({"event":"generation_attempted","method":name})
                raise AssertionError("Task 9 debug acceptance must not generate answers")
            return deny
        return getattr(self.provider,name)


EmbeddingProviderFactory.create=staticmethod(embedding)
LLMProviderFactory.create=staticmethod(lambda settings:GenerationGuard(original_llm(settings)))
if __name__=="__main__":uvicorn.run(app,host="127.0.0.1",port=8000)
