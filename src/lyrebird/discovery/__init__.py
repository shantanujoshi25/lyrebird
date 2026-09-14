"""discovery/ — the LLM-driven observe->decide->act loop.

This is the ONLY module that imports the LLM client. The whole value proposition depends on
the LLM living here and nowhere else: replay (the production path) must never call it. A
pytest in C5 asserts the replay package has no import path to the LLM client.

    llm.py      thin LLMClient protocol + AnthropicClient (model from env) + FakeLLMClient
    tools.py    the tool schemas the model may call (observe/click/type/.../finish)
    prompts.py  the system prompt + goal framing (incl. declared inputs + examples, A13)
    loop.py     the loop: observe -> decide -> policy check -> act, stop conditions, escalation
"""

from lyrebird.discovery.loop import DiscoveryLoop, DiscoveryResult
from lyrebird.discovery.llm import AnthropicClient, FakeLLMClient, LLMClient, ToolCall

__all__ = [
    "AnthropicClient",
    "DiscoveryLoop",
    "DiscoveryResult",
    "FakeLLMClient",
    "LLMClient",
    "ToolCall",
]
