#!/usr/bin/env python3
# api_server_hello.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Mixterioso Hello API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/hello")
def hello():
    return {"status": "ok", "message": "Hello from Mixterioso API"}

# end of api_server_hello.py
