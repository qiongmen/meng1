import configparser
import json
import platform
import subprocess

from openai import OpenAI

config = configparser.ConfigParser()
config.read('config.ini')

client = OpenAI(
    base_url=config['llm']['base_url'],
    api_key=config['llm']['api_key'],
)
model = config['llm']['model']


def getDate():
    """调用本地 date 命令获取当前电脑的日期和时间"""
    if platform.system() == 'Windows':
        cmd = ['powershell', '-NoProfile', '-Command', 'date']
    else:
        cmd = ['date']
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()


tools = [
    {
        'type': 'function',
        'function': {
            'name': 'getDate',
            'description': '获取当前电脑的日期和时间',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    }
]

tool_map = {'getDate': getDate}

messages = []
while True:
    user_input = input('你: ')
    messages.append({'role': 'user', 'content': user_input})

    while True:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            stream=True,
        )

        print('AI: ', end='', flush=True)
        content = ''
        tool_calls = {}
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                print(delta.content, end='', flush=True)
                content += delta.content
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    item = tool_calls.setdefault(tc.index, {'id': '', 'name': '', 'arguments': ''})
                    if tc.id:
                        item['id'] = tc.id
                    if tc.function:
                        if tc.function.name:
                            item['name'] = tc.function.name
                        if tc.function.arguments:
                            item['arguments'] += tc.function.arguments
        print()

        if not tool_calls:
            messages.append({'role': 'assistant', 'content': content})
            break

        assistant_tool_calls = []
        for index in sorted(tool_calls):
            tc = tool_calls[index]
            assistant_tool_calls.append({
                'id': tc['id'],
                'type': 'function',
                'function': {'name': tc['name'], 'arguments': tc['arguments']},
            })
        messages.append({'role': 'assistant', 'content': content or None, 'tool_calls': assistant_tool_calls})

        for tc in assistant_tool_calls:
            name = tc['function']['name']
            args = json.loads(tc['function']['arguments'] or '{}')
            output = tool_map[name](**args)
            print(f'[tool] {name} -> {output}')
            messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': str(output)})
