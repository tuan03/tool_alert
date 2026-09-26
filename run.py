import uvicorn
from app.config import Bootstrap
from app.main import create_app

if __name__ == "__main__":
    config = Bootstrap.from_env()
    uvicorn.run(create_app(config),host=config.host,port=config.port,workers=1,access_log=False)
