import configparser
import json
import os
import pathlib
import shutil

from openai import OpenAI

config = configparser.ConfigParser()
config.read('config.ini')

client = OpenAI(
    base_url=config['llm']['base_url'],
    api_key=config['llm']['api_key'],
)
model = config['llm']['model']


# ---------- 10 个文件/目录操作工具 ----------

def getcwd():
    """1. 获取当前工作目录"""
    return os.getcwd()


def listdir(path='.'):
    """2. 列出指定目录下所有文件和子目录"""
    p = pathlib.Path(path)
    if not p.exists():
        return f'错误: 路径不存在 {path}'
    if not p.is_dir():
        return f'错误: 不是目录 {path}'
    items = []
    for x in p.iterdir():
        kind = 'dir' if x.is_dir() else 'file'
        items.append(f'[{kind}] {x.name}')
    return '\n'.join(items) if items else '(空目录)'


def getFileType(filepath):
    """3. 获取指定文件的文件类型（扩展名 + 是否纯文本）"""
    p = pathlib.Path(filepath)
    if not p.exists():
        return f'错误: 文件不存在 {filepath}'
    if p.is_dir():
        return f'目录，类型: dir'
    ext = p.suffix.lower()
    text_exts = {'.txt', '.md', '.py', '.json', '.csv', '.log', '.ini', '.yaml', '.yml',
                 '.xml', '.html', '.htm', '.css', '.js', '.ts', '.java', '.c', '.cpp',
                 '.h', '.go', '.rs', '.sh', '.bat', '.ps1', '.mmd'}
    is_text = ext in text_exts
    try:
        with open(p, 'rb') as f:
            chunk = f.read(4096)
            if b'\x00' in chunk:
                is_text = False
    except Exception:
        pass
    return {
        'name': p.name,
        'extension': ext or '(无扩展名)',
        'size_bytes': p.stat().st_size,
        'is_text': is_text,
    }


def readFile(filepath):
    """4. 获取指定文件的内容（仅支持纯文本格式文件）"""
    p = pathlib.Path(filepath)
    if not p.exists():
        return f'错误: 文件不存在 {filepath}'
    if p.is_dir():
        return f'错误: 是目录不是文件 {filepath}'
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        return f'错误: 读取失败 {e}'


def createFile(dirpath, filename, content=''):
    """5. 根据指定的目录和文件名创建新文件"""
    d = pathlib.Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    f = d / filename
    with open(f, 'w', encoding='utf-8') as fp:
        fp.write(content)
    return f'已创建 {f}'


def writeFile(filepath, content):
    """6. 编辑指定文件的内容（覆盖写入，仅支持纯文本格式文件）"""
    p = pathlib.Path(filepath)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)
    return f'已写入 {p}'


def mkdir(dirpath):
    """7. 创建新目录"""
    p = pathlib.Path(dirpath)
    p.mkdir(parents=True, exist_ok=True)
    return f'已创建目录 {p}'


def rmdir(dirpath):
    """8. 删除目录（非空，递归删除）"""
    p = pathlib.Path(dirpath)
    if not p.exists():
        return f'错误: 目录不存在 {dirpath}'
    if not p.is_dir():
        return f'错误: 不是目录 {dirpath}'
    shutil.rmtree(p)
    return f'已删除目录 {p}（含所有内容）'


def rmFile(filepath):
    """9. 删除文件"""
    p = pathlib.Path(filepath)
    if not p.exists():
        return f'错误: 文件不存在 {filepath}'
    if p.is_dir():
        return f'错误: 是目录，请用 rmdir {filepath}'
    p.unlink()
    return f'已删除文件 {p}'


def rename(src, dst):
    """10. 重命名文件或目录"""
    s = pathlib.Path(src)
    d = pathlib.Path(dst)
    if not s.exists():
        return f'错误: 源不存在 {src}'
    s.rename(d)
    return f'已重命名 {s} -> {d}'


# ---------- Tool Schema ----------

tools = [
    {
        'type': 'function',
        'function': {
            'name': 'getcwd',
            'description': '获取当前工作目录（进程启动时的目录）',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'listdir',
            'description': '列出指定目录下所有文件和子目录',
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': '目录路径，默认当前目录 "." '},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'getFileType',
            'description': '获取指定文件的文件类型（扩展名、大小、是否纯文本）',
            'parameters': {
                'type': 'object',
                'properties': {
                    'filepath': {'type': 'string', 'description': '文件路径'},
                },
                'required': ['filepath'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'readFile',
            'description': '获取指定文件的内容（仅支持纯文本格式文件）',
            'parameters': {
                'type': 'object',
                'properties': {
                    'filepath': {'type': 'string', 'description': '文件路径'},
                },
                'required': ['filepath'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'createFile',
            'description': '根据指定的目录和文件名创建新文件',
            'parameters': {
                'type': 'object',
                'properties': {
                    'dirpath': {'type': 'string', 'description': '目录路径'},
                    'filename': {'type': 'string', 'description': '文件名'},
                    'content': {'type': 'string', 'description': '文件初始内容，默认为空'},
                },
                'required': ['dirpath', 'filename'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'writeFile',
            'description': '编辑指定文件的内容（覆盖写入，仅支持纯文本）',
            'parameters': {
                'type': 'object',
                'properties': {
                    'filepath': {'type': 'string', 'description': '文件路径'},
                    'content': {'type': 'string', 'description': '要写入的完整内容'},
                },
                'required': ['filepath', 'content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'mkdir',
            'description': '创建新目录（支持递归创建父目录）',
            'parameters': {
                'type': 'object',
                'properties': {
                    'dirpath': {'type': 'string', 'description': '目录路径'},
                },
                'required': ['dirpath'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'rmdir',
            'description': '删除目录（非空，递归删除其下所有内容）',
            'parameters': {
                'type': 'object',
                'properties': {
                    'dirpath': {'type': 'string', 'description': '目录路径'},
                },
                'required': ['dirpath'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'rmFile',
            'description': '删除文件',
            'parameters': {
                'type': 'object',
                'properties': {
                    'filepath': {'type': 'string', 'description': '文件路径'},
                },
                'required': ['filepath'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'rename',
            'description': '重命名文件或目录',
            'parameters': {
                'type': 'object',
                'properties': {
                    'src': {'type': 'string', 'description': '源路径'},
                    'dst': {'type': 'string', 'description': '目标路径'},
                },
                'required': ['src', 'dst'],
            },
        },
    },
]

tool_map = {
    'getcwd': getcwd,
    'listdir': listdir,
    'getFileType': getFileType,
    'readFile': readFile,
    'createFile': createFile,
    'writeFile': writeFile,
    'mkdir': mkdir,
    'rmdir': rmdir,
    'rmFile': rmFile,
    'rename': rename,
}


# ---------- 主循环 ----------

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
