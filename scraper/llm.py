from openai import AsyncOpenAI, OpenAI

MODEL = "gpt-5.4-mini"


def sync_client() -> OpenAI:
    return OpenAI()


def async_client() -> AsyncOpenAI:
    return AsyncOpenAI()
