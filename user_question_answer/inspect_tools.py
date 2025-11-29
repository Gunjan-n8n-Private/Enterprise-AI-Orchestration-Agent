from google.adk.tools import load_memory, preload_memory, FunctionTool

print(f"load_memory type: {type(load_memory)}")
print(f"preload_memory type: {type(preload_memory)}")

print(f"load_memory dir: {dir(load_memory)}")
print(f"preload_memory dir: {dir(preload_memory)}")

try:
    print(f"load_memory name: {load_memory.name}")
except:
    pass

try:
    print(f"preload_memory name: {preload_memory.name}")
except:
    pass
