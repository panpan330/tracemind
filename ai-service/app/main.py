from fastapi import FastAPI

app = FastAPI(title="TraceMind AI Service")


@app.get("/api/health")
def health():
    return {"status": "ok"}
