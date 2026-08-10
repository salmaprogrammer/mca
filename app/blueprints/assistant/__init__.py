from flask import Blueprint

bp = Blueprint("assistant", __name__, url_prefix="/assistant")

from app.blueprints.assistant import (
    course_routes,
    enrollment_routes,
    routes,
    session_routes,
    teaching_routes,
    whatsapp_routes,
)
