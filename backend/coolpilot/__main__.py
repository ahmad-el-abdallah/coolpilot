"""Run the server: python -m coolpilot  (waitress, localhost only)."""
from waitress import serve

from .app import create_app
from .security import PORT

if __name__ == "__main__":
    serve(create_app(), host="127.0.0.1", port=PORT, threads=8)
