"""Notebook chat replays Mistral thinking chunks and shows the answer only."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_mistralai.chat_models import (
    _convert_message_to_mistral_chat_message,
    _convert_mistral_chat_message_to_message,
)

from api.routers._chat_shared import extract_chat_messages
from open_notebook.exceptions import IncompleteGenerationError
from open_notebook.graphs.chat import ThreadState, call_model_with_messages

THINKING = "REPLAY-THINKING-7c2e"
ANSWER = "Visible answer"


def _state(messages: list) -> ThreadState:
    return {
        "messages": messages,
        "notebook": None,
        "context": None,
        "context_config": None,
        "model_override": None,
    }


def _first_block(message: object) -> dict:
    content = getattr(message, "content")
    assert isinstance(content, list)
    block = content[0]
    assert isinstance(block, dict)
    return block


def _thinking_reply() -> AIMessage:
    return AIMessage(
        id="ai-1",
        content=[
            {
                "type": "thinking",
                "thinking": [{"type": "text", "text": THINKING}],
                "signature": "sig-1",
            },
            {"type": "text", "text": ANSWER},
        ],
    )


def test_mistral_parser_round_trips_thinking_chunks() -> None:
    raw = {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": [{"type": "text", "text": THINKING}],
                "signature": "sig-1",
            },
            {"type": "text", "text": ANSWER},
        ],
    }

    message = _convert_mistral_chat_message_to_message(raw)
    sent = _convert_message_to_mistral_chat_message(message)

    assert _first_block(message)["type"] == "thinking"
    assert sent["role"] == "assistant"
    assert sent["content"][0] == {
        "type": "thinking",
        "thinking": [{"type": "text", "text": THINKING}],
        "signature": "sig-1",
    }
    assert sent["content"][1] == {"type": "text", "text": ANSWER}


def test_notebook_chat_keeps_thinking_in_the_checkpoint_and_hides_it() -> None:
    with patch(
        "open_notebook.graphs.chat.provision_langchain_model",
        new=AsyncMock(return_value=_model_returning(_thinking_reply())),
    ):
        result = call_model_with_messages(
            _state([HumanMessage(content="hi")]), {"configurable": {}}
        )

    stored = result["messages"]
    assert _first_block(stored)["thinking"][0]["text"] == THINKING
    assert _first_block(stored)["signature"] == "sig-1"

    shown = extract_chat_messages([stored])
    assert shown[0].content == ANSWER
    assert THINKING not in shown[0].content


def test_next_turn_sends_the_thinking_chunks_again() -> None:
    first = _thinking_reply()
    payloads: list = []

    def invoke(payload):
        payloads.append(payload)
        return first

    model = MagicMock()
    model.invoke = invoke
    with patch(
        "open_notebook.graphs.chat.provision_langchain_model",
        new=AsyncMock(return_value=model),
    ):
        opened = call_model_with_messages(
            _state([HumanMessage(content="hi")]),
            {"configurable": {}},
        )
        call_model_with_messages(
            _state(
                [
                    HumanMessage(content="hi"),
                    opened["messages"],
                    HumanMessage(content="and then?"),
                ]
            ),
            {"configurable": {}},
        )

    assistant = next(message for message in payloads[-1] if message.type == "ai")
    sent = _convert_message_to_mistral_chat_message(assistant)
    assert sent["content"][0]["type"] == "thinking"
    assert sent["content"][0]["thinking"][0]["text"] == THINKING
    assert sent["content"][0]["signature"] == "sig-1"


def test_thinking_only_reply_is_not_checkpointed() -> None:
    reply = AIMessage(
        content=[{"type": "thinking", "thinking": [{"type": "text", "text": THINKING}]}]
    )
    with patch(
        "open_notebook.graphs.chat.provision_langchain_model",
        new=AsyncMock(return_value=_model_returning(reply)),
    ):
        with pytest.raises(IncompleteGenerationError, match="empty response"):
            call_model_with_messages(
                _state([HumanMessage(content="hi")]), {"configurable": {}}
            )


def test_shown_human_text_keeps_a_stray_think_tag() -> None:
    source = "The paper says </think> and then the result."
    shown = extract_chat_messages([HumanMessage(id="human-1", content=source)])
    assert shown[0].content == source


def _model_returning(reply: AIMessage) -> MagicMock:
    model = MagicMock()
    model.invoke = MagicMock(return_value=reply)
    return model
