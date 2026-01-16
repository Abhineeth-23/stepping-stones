from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import uuid
from datetime import datetime, date


db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    
    # --- NEW FIELDS ---
    email = db.Column(db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    dob = db.Column(db.Date, nullable=True)
    # ------------------
    
    password = db.Column(db.String(255), nullable=False)
    is_dark_mode = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Streak Logic
    daily_target = db.Column(db.Integer, default=2)
    current_streak = db.Column(db.Integer, default=0)
    last_streak_date = db.Column(db.Date, nullable=True)
    streak_freezes = db.Column(db.Integer, default=3)
    
    # Weekly Rest Days (e.g. "5,6")
    rest_days = db.Column(db.String(20), default="") 

    # Relationships
    steps = db.relationship('Step', backref='user', lazy=True)
    global_journals = db.relationship('GlobalJournal', backref='user', lazy=True)
    step_logs = db.relationship('StepLog', backref='user', lazy=True)
    custom_rest_days = db.relationship('CustomRestDay', backref='user', lazy=True)

class Step(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    overview_content = db.Column(db.Text, default="")
    category = db.Column(db.String(100), default="General") 
    timeframe = db.Column(db.String(50), default="Weekly") 
    deadline_mode = db.Column(db.String(50), default="rolling")
    deadline_date = db.Column(db.Date, nullable=True) 
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    share_token = db.Column(db.String(50), unique=True, nullable=True)

    logs = db.relationship('StepLog', backref='step', cascade="all, delete", lazy=True)
    subtasks = db.relationship('SubTask', backref='step', cascade="all, delete", lazy=True)

class CustomRestDay(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class StepLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    date = db.Column(db.Date, default=date.today, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    step_id = db.Column(db.Integer, db.ForeignKey('step.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class GlobalJournal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=True)
    content = db.Column(db.Text, nullable=False)
    date = db.Column(db.Date, default=date.today, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class SubTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(200), nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    step_id = db.Column(db.Integer, db.ForeignKey('step.id'), nullable=False)