from flask import Blueprint

bp = Blueprint("portal", __name__, url_prefix="/portal")

from app.blueprints.portal import routes
