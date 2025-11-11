
from beam import Pod, Image

app = Pod(
    image=Image.from_dockerfile('Dockerfile'),
    entrypoint=['uvicorn', 'app:app', '--host', '0.0.0.0', '--port', '8000'],
    ports=[8000],
    tcp=False,
    env=[]
)
