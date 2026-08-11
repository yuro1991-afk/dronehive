# Multi-Host FACE

**Law:** only the FACE speaks to the user. Every other model/host is internal.

```
User
  ↕  only
FACE  (llama3.1:8b)
  ↑ briefs only
Workers: Ollama · Hermes · OpenClaw · OpenCode · Super Cell · Drones · Everest · cloud opt-in
```

## Hosts registered

| id | kind | role |
|----|------|------|
| ollama_fast | Ollama 3b | intent scout |
| ollama_coder | Ollama coder 7b | code worker |
| ollama_ops | Ollama 7b | ops |
| ollama_smarts | ai-smarts | ontology |
| ollama_seal | mistral 7b | critic |
| hermes | Hermes agent | hermes worker |
| openclaw | OpenClaw | openclaw worker |
| opencode | OpenCode | opencode worker |
| supercell_muscle | Jane muscle | probe helper |
| drones | DroneHive fast | drone worker |
| everest | Everest bridge | status |
| spacexai / gemini | cloud | opt-in only |

## CLI

```powershell
cd G:\AI-Home\projects\ai-worker-drone-0.5b
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

# who is up
& $py -m drone face hosts

# user message → council → FACE reply
& $py -m drone face ask "how do I fix a python import error"

# force specific workers
& $py -m drone face ask "status check" --workers ollama_fast,hermes,openclaw,everest

# TUI stream lines
& $py -m drone face ask "hello" --stream

& $py -m drone face seal
```

## Paths

- Config: `configs/multi_hosts.json`
- Live: `out/MULTI_HOSTS_LIVE.json`
- Last reply: `out/MULTI_FACE_LAST.json`
- Seal: `out/MULTI_FACE_SEAL.json`

## Honesty

- Down hosts → SKIP, not fake text  
- Workers never write final user channel  
- 12GB: max one heavy Ollama worker + face  
