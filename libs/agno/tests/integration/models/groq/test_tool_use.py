from typing import Optional

import pytest
from pydantic import BaseModel, Field

from agno.agent import Agent, RunResponse  # noqa
from agno.models.groq import Groq
from agno.tools.exa import ExaTools
from agno.tools.yfinance import YFinanceTools


def test_tool_use():
    agent = Agent(
        model=Groq(id="gemma2-9b-it"),
        tools=[YFinanceTools(cache_results=True)],
        markdown=True,
        telemetry=False,
        monitoring=False,
    )

    response = agent.run("What is the current price of TSLA?")

    # Verify tool usage
    assert any(msg.tool_calls for msg in response.messages)
    assert response.content is not None


@pytest.mark.skip(reason="This test is flaky.")
def test_tool_use_stream():
    agent = Agent(
        model=Groq(id="llama-3.3-70b-versatile"),
        tools=[YFinanceTools(cache_results=True)],
        markdown=True,
        telemetry=False,
        monitoring=False,
    )

    response_stream = agent.run("What is the current price of TSLA?", stream=True, stream_intermediate_steps=True)

    responses = []
    tool_call_seen = False

    for chunk in response_stream:
        responses.append(chunk)
        if chunk.tools:
            if any(tc.tool_name for tc in chunk.tools):
                tool_call_seen = True

    assert len(responses) > 0
    assert tool_call_seen, "No tool calls observed in stream"


@pytest.mark.asyncio
async def test_async_tool_use():
    agent = Agent(
        model=Groq(id="gemma2-9b-it"),
        tools=[YFinanceTools(cache_results=True)],
        markdown=True,
        telemetry=False,
        monitoring=False,
    )

    response = await agent.arun("What is the current price of TSLA?")

    # Verify tool usage
    assert any(msg.tool_calls for msg in response.messages if msg.role == "assistant")
    assert response.content is not None


@pytest.mark.asyncio
async def test_async_tool_use_stream():
    agent = Agent(
        model=Groq(id="gemma2-9b-it"),
        tools=[YFinanceTools(cache_results=True)],
        markdown=True,
        telemetry=False,
        monitoring=False,
    )

    response_stream = await agent.arun(
        "What is the current price of TSLA?", stream=True, stream_intermediate_steps=True
    )

    responses = []
    tool_call_seen = False

    async for chunk in response_stream:
        responses.append(chunk)

        # Check for ToolCallStartedEvent or ToolCallCompletedEvent
        if chunk.event in ["ToolCallStarted", "ToolCallCompleted"] and hasattr(chunk, "tool") and chunk.tool:
            if chunk.tool.tool_name:
                tool_call_seen = True

    assert len(responses) > 0
    assert tool_call_seen, "No tool calls observed in stream"


@pytest.mark.skip(reason="This test is flaky.")
def test_parallel_tool_calls():
    agent = Agent(
        model=Groq(id="gemma2-9b-it"),
        tools=[YFinanceTools(cache_results=True)],
        markdown=True,
        telemetry=False,
        monitoring=False,
    )

    response = agent.run("What is the current price of TSLA and AAPL?")

    # Verify tool usage
    tool_calls = []
    for msg in response.messages:
        if msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
    assert len([call for call in tool_calls if call.get("type", "") == "function"]) >= 2  # Total of 2 tool calls made
    assert response.content is not None


@pytest.mark.skip(reason="Groq does not support native structured outputs for tool calls at this time.")
def test_tool_use_with_native_structured_outputs():
    class StockPrice(BaseModel):
        price: float = Field(..., description="The price of the stock")
        currency: str = Field(..., description="The currency of the stock")

    agent = Agent(
        model=Groq(id="llama-3.3-70b-versatile"),
        tools=[YFinanceTools(cache_results=True)],
        markdown=True,
        response_model=StockPrice,
        telemetry=False,
        monitoring=False,
    )
    response = agent.run("What is the current price of TSLA?")
    assert isinstance(response.content, StockPrice)
    assert response.content is not None
    assert response.content.price is not None
    assert response.content.currency is not None


def test_tool_call_custom_tool_no_parameters():
    def get_the_weather_in_tokyo():
        """
        Get the weather in Tokyo
        """
        return "It is currently 70 degrees and cloudy in Tokyo"

    agent = Agent(
        model=Groq(id="gemma2-9b-it"),
        tools=[get_the_weather_in_tokyo],
        markdown=True,
        telemetry=False,
        monitoring=False,
    )

    response = agent.run("What is the weather in Tokyo?")

    # Verify tool usage
    assert any(msg.tool_calls for msg in response.messages)
    assert response.content is not None
    assert "70" in response.content


def test_tool_call_custom_tool_optional_parameters():
    def get_the_weather(city: Optional[str] = None):
        """
        Get the weather in a city

        Args:
            city: The city to get the weather for
        """
        if city is None:
            return "It is currently 70 degrees and cloudy in Tokyo"
        else:
            return f"It is currently 70 degrees and cloudy in {city}"

    agent = Agent(
        model=Groq(id="gemma2-9b-it"),
        tools=[get_the_weather],
        markdown=True,
        telemetry=False,
        monitoring=False,
    )

    response = agent.run("What is the weather in Paris?")

    # Verify tool usage
    assert any(msg.tool_calls for msg in response.messages)
    assert response.content is not None
    assert "70" in response.content


def test_tool_call_list_parameters():
    agent = Agent(
        model=Groq(id="gemma2-9b-it"),
        tools=[ExaTools()],
        instructions="Use a single tool call if possible",
        markdown=True,
        telemetry=False,
        monitoring=False,
    )

    response = agent.run(
        "What are the papers at https://arxiv.org/pdf/2307.06435 and https://arxiv.org/pdf/2502.09601 about?"
    )

    # Verify tool usage
    assert any(msg.tool_calls for msg in response.messages)
    tool_calls = []
    for msg in response.messages:
        if msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
    for call in tool_calls:
        if call.get("type", "") == "function":
            assert call["function"]["name"] in ["search_exa", "get_contents", "exa_answer"]
    assert response.content is not None
