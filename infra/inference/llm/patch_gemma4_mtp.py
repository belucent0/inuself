"""Backport vLLM main's target-width embedding fix for Gemma 4 MTP."""

from pathlib import Path


path = Path(
    "/usr/local/lib/python3.12/dist-packages/vllm/v1/spec_decode/llm_base_proposer.py"
)
source = path.read_text()
old = "            if share_embeddings:\n                draft_embed = self.model.model.embed_tokens"
new = (
    '            if share_embeddings and hasattr(self.model, "has_own_embed_tokens"):\n'
    "                draft_embed = self.model.model.embed_tokens"
)
if new not in source:
    if source.count(old) != 1:
        raise RuntimeError("unexpected vLLM proposer source")
    path.write_text(source.replace(old, new))
