
from beam import Pod

app = Pod(
    entrypoint=['uvicorn', 'app:app', '--host', '0.0.0.0', '--port', '$PORT'],
    tcp=False,
    env=[],
    dockerfile=None
)
