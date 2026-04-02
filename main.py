# main.py
from fastapi import FastAPI
from routes.match import router as match_router

app = FastAPI()

# Load routers from the routes/ directory
app.include_router(match_router)

@app.get("/")
def read_root():
    return {"Hello": "World"}
