

from fastapi import FastAPI, HTTPException
import uvicorn



app = FastAPI(
    title="Imena News Box API",
    description="API for managing news articles and categories in the Imena News Box project.",
    version="2.0.0"
)



@app.get("/health")
def health_check():
    return {"status": "API is healthy"}


@app.get("/")
def read_indexRoot() -> dict:
    """
    Root endpoint that returns a welcome message.
    """
    return {"message": "Welcome to the Imena News Box API!"}


if __name__ == "__main__" :
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="info", reload=True)



