import configparser
from openai import OpenAI

config = configparser.ConfigParser()
config.read('config.ini')

client = OpenAI(
    base_url=config['llm']['base_url'],
    api_key=config['llm']['api_key'],
)

while True:
    user_input = input("你: ")
    stream = client.chat.completions.create(
        model=config['llm']['model'],
        messages=[{"role": "user", "content": user_input}],
        stream=True,
    )
    print("AI: ", end="", flush=True)
    for chunk in stream:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)
    print()
