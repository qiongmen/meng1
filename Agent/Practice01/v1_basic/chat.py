import configparser
from openai import OpenAI

config = configparser.ConfigParser()
config.read('config.ini')

client = OpenAI(
    base_url=config['llm']['base_url'],
    api_key=config['llm']['api_key'],
)

user_input = input("你: ")
response = client.chat.completions.create(
    model=config['llm']['model'],
    messages=[{"role": "user", "content": user_input}],
)
print("AI:", response.choices[0].message.content)

