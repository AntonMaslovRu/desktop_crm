from envo.api.app import create_app
from envo.config import Settings

app = create_app(Settings.load().db_dsn)
