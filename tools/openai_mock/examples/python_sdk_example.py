from openai import OpenAI

client = OpenAI(
    api_key="mock-key",
    base_url="http://127.0.0.1:8000/v1",
    max_retries=0,
)

response = client.responses.create(
    model="mock-gpt",
    input="hello from Python SDK",
)

print(response.output_text)
