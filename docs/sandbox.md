# The Code step sandbox

Code steps (and Code steps an Agent uses as tools) run Python that you, a teammate, a template,
or an AI wrote. In Easy Chain that code runs in a **sandbox**: a fresh, locked-down container
for every call. Exported code runs Code steps in its own process.

## What the code can't do

| | In the sandbox |
|---|---|
| This computer's files | Not there at all: the container has its own file system, read-only. `/tmp` is a small scratch space (64 MB), thrown away afterwards. |
| The network | None, unless the step's **Let it use the internet** is on (Pro, *More options*). |
| Secrets and keys | Not passed in. Easy Chain's environment, API keys and vault stay outside. Steps that need a key should use a Web request with `{secret:NAME}`. |
| Docker itself | The Docker socket isn't inside, so the code can't start containers. |
| Privileges | Runs as an unprivileged user (`nobody`), with every Linux capability dropped and no way to gain more. |
| Memory, time, processes | Limited (defaults: 256 MB, 120 s, 64 processes; a step's own time limit applies when it is shorter). Going over stops the code with a clear message. |
| The kernel | With **gVisor** (`runsc`) installed in Docker, the code talks to gVisor's kernel, not this computer's. Easy Chain uses it automatically when Docker has it. |

Each of these is tested by trying to break out (`python/tests/test_sandbox.py`): reading a host
file, writing outside `/tmp`, opening connections, reading the environment, becoming root, a
fork bomb, allocating too much memory and running forever.

## What it needs

**Docker** with Linux containers: Docker Engine on Linux, Docker Desktop on Windows and macOS.
Easy Chain uses the `docker` command, so nothing else is needed.

Open **Settings → Sandbox** to see whether Docker was found, and press **Prepare the sandbox** to
download the image (`python:3.12-slim`) before the first Code step needs it.

## Settings

| Setting | What it does |
|---|---|
| Run Code steps in the sandbox | **Use Docker when it's there** (default): without Docker, Code steps run in Easy Chain's own process, and the check before a run says so on the step. **Always**: Code steps fail without Docker (for servers). **Off**: run them in-process, as exported code does; only for code you trust completely. |
| Memory, Time limit | Per call. |
| CPUs, Most processes, Image, Container runtime *(Pro)* | The image must be a Linux image with Python 3; gVisor (`runsc`) can be required or turned off. |

The same settings come from the environment where there is no Settings page:

| Variable | Values |
|---|---|
| `EASYCHAIN_SANDBOX` | `auto` (default), `docker` (always), `off` |
| `EASYCHAIN_SANDBOX_IMAGE` | a Linux image with Python 3 (default `python:3.12-slim`) |
| `EASYCHAIN_SANDBOX_RUNTIME` | `auto` (gVisor when Docker has it), `runsc`, `runc` |

`easychain run` and `easychain test` take `--sandbox auto|docker|off`.

## Writing code for the sandbox

- **Flow Data comes in as plain values.** Text, numbers, lists and dicts are as they are. Chat
  messages arrive as dicts (`{"role": "user", "content": "…"}`) that also allow
  `message.content` and `message.type`. Anything else (dates, objects) arrives as text.
- **Packages:** only Python's standard library is there. List what the code imports under
  **Packages** (Pro): Easy Chain builds an image with them, once, and reuses it. A missing
  package fails with "add … to this step's Packages".
- **The internet:** off. Turn on **Let it use the internet** for code that must call a website
  itself; a Web request step is usually the better choice.
- **Run it at most once** works the same: `idempotency_key()` is available in the code.
- **Printing** is fine: printed text goes to the log, not into the result.
- Each call starts a container, which adds about half a second to a few seconds (more on
  gVisor). For Each over many items runs many containers, side by side.

## With Docker Compose

`docker compose up` runs the worker in a container, where there is no Docker, so Code steps run
in-process there and the check before a run says so. To use the sandbox, add the overlay:

```bash
DOCKER_GID=$(getent group docker | cut -d: -f3) \
  docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up
```

It gives the worker (and the API, for Settings → Sandbox) the host's Docker socket and sets the
sandbox to **Always**. A process that can use the Docker socket can do anything on the host, so
only run Easy Chain where you trust who can reach it; the sandbox containers never see the
socket.

## Exported code

The exported project runs Code steps in its own process, like any LangGraph app; its header
says so. Run it only with code you trust, or in your own container.
