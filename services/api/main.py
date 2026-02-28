from fastapi import FastAPI
import os

app = FastAPI(title="AIC - AI Incident Commander")

@app.get("/")
def health():
    return {
        "service": "AIC API",
        "status": "healthy",
        "environment": os.getenv("ENVIRONMENT", "dev")
    }
