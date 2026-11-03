# term:LLM 接入  tool:llm_chat  doc:doc/功能/LLM接入/说明.md
from fawai_avatar.llm.asr import mime_for_audio, transcribe_audio
from fawai_avatar.llm.client import LlmClient, chat
from fawai_avatar.llm.router import messages_need_vision, route_model

__all__ = [
    "LlmClient",
    "chat",
    "messages_need_vision",
    "mime_for_audio",
    "route_model",
    "transcribe_audio",
]
