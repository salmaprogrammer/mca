from flask import Blueprint

bp = Blueprint("teacher", __name__, url_prefix="/teacher")

from app.blueprints.teacher import routes
