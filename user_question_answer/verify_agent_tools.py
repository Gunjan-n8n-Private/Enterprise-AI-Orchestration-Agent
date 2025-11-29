from agent import root_agent

print("Verifying root_agent tools...")
tool_names = [t.name for t in root_agent.tools]
print(f"Tool names: {tool_names}")

if "preload_memory" in tool_names:
    print("SUCCESS: preload_memory is in the tools list.")
else:
    print("FAILURE: preload_memory is MISSING from the tools list.")
