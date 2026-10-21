import configparser
from openai import OpenAI

config = configparser.ConfigParser()
config.read('config.ini')

client = OpenAI(
    base_url=config['llm']['base_url'],
    api_key=config['llm']['api_key'],
)

messages = []
while True:
    user_input = input("你: ")
    messages.append({"role": "user", "content": user_input})
    stream = client.chat.completions.create(
        model=config['llm']['model'],
        messages=messages,
        stream=True,
    )
    print("AI: ", end="", flush=True)
    reply = ""
    for chunk in stream:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)
            reply += chunk.choices[0].delta.content
    print()
    messages.append({"role": "assistant", "content": reply})
