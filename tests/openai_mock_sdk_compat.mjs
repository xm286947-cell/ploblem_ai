import assert from "node:assert/strict";
import OpenAI from "openai";

const baseURL = process.env.OPENAI_MOCK_BASE_URL ?? "http://127.0.0.1:8765/v1";
const controlURL = baseURL.replace(/\/v1\/?$/, "");

async function configure(scenarioKey, payload, behavior = {}) {
  const response = await fetch(`${controlURL}/__mock__/scenario`, {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({
      scenario_key: scenarioKey,
      payload,
      behavior,
    }),
  });
  assert.equal(response.status, 200);
}

function options(scenarioKey) {
  return {headers: {"X-Mock-Scenario-Key": scenarioKey}};
}

const client = new OpenAI({
  apiKey: "mock-key",
  baseURL,
  maxRetries: 0,
});

await configure("js-responses", "JS responses OK");
const response = await client.responses.create(
  {model: "mock-gpt", input: "hello"},
  options("js-responses"),
);
assert.equal(response.output_text, "JS responses OK");

await configure("js-chat", "JS chat OK");
const chat = await client.chat.completions.create(
  {
    model: "mock-gpt",
    messages: [{role: "user", content: "hello"}],
  },
  options("js-chat"),
);
assert.equal(chat.choices[0].message.content, "JS chat OK");

const models = await client.models.list();
assert.equal(models.data[0].id, "mock-gpt");

await configure("js-responses-stream", "responses stream");
const responseStream = await client.responses.create(
  {model: "mock-gpt", input: "hello", stream: true},
  options("js-responses-stream"),
);
let responseText = "";
let responseCompleted = false;
for await (const event of responseStream) {
  if (event.type === "response.output_text.delta") {
    responseText += event.delta;
  }
  if (event.type === "response.completed") {
    responseCompleted = true;
  }
}
assert.equal(responseText, "responses stream");
assert.equal(responseCompleted, true);

await configure("js-chat-stream", "chat stream");
const chatStream = await client.chat.completions.create(
  {
    model: "mock-gpt",
    messages: [{role: "user", content: "hello"}],
    stream: true,
  },
  options("js-chat-stream"),
);
let chatText = "";
for await (const chunk of chatStream) {
  chatText += chunk.choices?.[0]?.delta?.content ?? "";
}
assert.equal(chatText, "chat stream");

console.log("OpenAI JS SDK compatibility: 5/5 PASS");
