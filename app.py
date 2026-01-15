from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, Step, StepLog, GlobalJournal, SubTask
from datetime import datetime, date, timedelta
import calendar
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'stepping_stones_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_today():
    return {'date': date}

# ==========================================
#              HELPER FUNCTIONS
# ==========================================

def get_heatmap_data(user):
    """
    Fetches all logs for the user to populate the GitHub-style heatmap.
    """
    logs = db.session.query(StepLog.date, db.func.count(StepLog.id))\
        .filter(StepLog.user_id == user.id)\
        .group_by(StepLog.date).all()
        
    data = {}
    for log_date, count in logs:
        data[log_date.strftime('%Y-%m-%d')] = count
        
    return data

def calculate_deadline(timeframe, mode):
    """
    Calculates the target date based on User selection.
    """
    today = date.today()
    
    if mode == 'rolling':
        # Simple addition: Today + X days
        if timeframe == 'Weekly':
            return today + timedelta(days=7)
        if timeframe == 'Monthly':
            return today + timedelta(days=30)
        if timeframe == 'Yearly':
            return today + timedelta(days=365)
    
    elif mode == 'calendar':
        # End of CURRENT period logic
        if timeframe == 'Weekly':
            days_ahead = 6 - today.weekday()
            if days_ahead < 0:
                days_ahead += 7
            return today + timedelta(days=days_ahead)
            
        if timeframe == 'Monthly':
            last_day = calendar.monthrange(today.year, today.month)[1]
            return date(today.year, today.month, last_day)
            
        if timeframe == 'Yearly':
            return date(today.year, 12, 31)
            
    return today + timedelta(days=7) # Default fallback

def update_streak_status(user):
    """
    Handles logic for Daily Streaks, Freezes, and Rest Days.
    """
    today = date.today()
    
    # Check if we need to reset, freeze, or apply rest day logic
    if user.last_streak_date:
        delta = (today - user.last_streak_date).days
        
        # If delta > 1, it means they missed at least one full day
        if delta > 1:
            yesterday = today - timedelta(days=1)
            
            # Check if yesterday was a designated Rest Day
            # 0=Monday, 6=Sunday
            is_protected_by_rest = False
            if user.rest_days:
                rest_days_list = user.rest_days.split(',')
                if str(yesterday.weekday()) in rest_days_list:
                    is_protected_by_rest = True
            
            if is_protected_by_rest:
                # Do nothing (Streak is safe)
                pass
            elif user.streak_freezes > 0:
                # Use a freeze
                user.streak_freezes -= 1
                user.last_streak_date = today - timedelta(days=1)
                flash(f"🧊 Missed a day! Freeze used. ({user.streak_freezes} left)", "info")
            else:
                # Reset streak
                user.current_streak = 0
                flash("Streak reset! No freezes left.", "error")

    # Calculate Today's Progress
    todays_count = StepLog.query.filter_by(user_id=user.id, date=today).group_by(StepLog.step_id).count()
    
    if todays_count >= user.daily_target:
        # Only increment if we haven't already done it today
        if user.last_streak_date != today:
            user.current_streak += 1
            user.last_streak_date = today
    
    db.session.commit()
    return todays_count

# ==========================================
#               MAIN ROUTES
# ==========================================

@app.route('/')
@login_required
def dashboard():
    today = date.today()
    progress = update_streak_status(current_user)
    
    steps = Step.query.filter_by(user_id=current_user.id, is_active=True).limit(4).all()
    daily_journal = GlobalJournal.query.filter_by(user_id=current_user.id, date=today).first()
    
    heatmap_data = get_heatmap_data(current_user)
    
    return render_template(
        'dashboard.html', 
        user=current_user, 
        steps=steps, 
        daily_journal=daily_journal,
        progress=progress,
        today_date=today,
        heatmap_data=heatmap_data,
        user_created_at=current_user.created_at.strftime('%Y-%m-%d')
    )

# ==========================================
#           PHASE 4: NEW FEATURES
# ==========================================

@app.route('/settings/rest_days', methods=['POST'])
@login_required
def set_rest_days():
    """
    Updates the user's preferred rest days (e.g., Sat/Sun).
    """
    days = request.form.getlist('rest_days') # Returns list like ['5', '6']
    current_user.rest_days = ",".join(days)
    
    db.session.commit()
    flash("Rest days updated! Enjoy your time off.", "success")
    return redirect(url_for('dashboard'))

@app.route('/step/share/<int:step_id>')
@login_required
def generate_share_link(step_id):
    """
    Generates a unique public link for a specific goal.
    """
    step = Step.query.get_or_404(step_id)
    
    if step.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Generate token if it doesn't exist
    if not step.share_token:
        step.share_token = str(uuid.uuid4())[:8] # Short 8-char token
        db.session.commit()
    
    # Create full URL
    link = url_for('view_shared_step', token=step.share_token, _external=True)
    return jsonify({'link': link})

@app.route('/shared/<token>')
def view_shared_step(token):
    """
    Public read-only view for accountability partners.
    """
    step = Step.query.filter_by(share_token=token).first_or_404()
    history = StepLog.query.filter_by(step_id=step.id).order_by(StepLog.date.desc()).all()
    
    return render_template('shared_step.html', step=step, history=history)

@app.route('/calendar')
@login_required
def calendar_view():
    """
    Time Travel view: Shows all history linearly.
    """
    logs = StepLog.query.filter_by(user_id=current_user.id).order_by(StepLog.date.desc()).all()
    return render_template('calendar_view.html', logs=logs)

# ==========================================
#          STANDARD STEP ROUTES
# ==========================================

@app.route('/steps')
@login_required
def all_steps():
    filter_type = request.args.get('filter', 'active')
    sort_by = request.args.get('sort', 'newest')
    
    query = Step.query.filter_by(user_id=current_user.id)
    
    # Apply Filtering
    if filter_type == 'active':
        query = query.filter_by(is_active=True)
    elif filter_type == 'archived':
        query = query.filter_by(is_active=False)
    elif filter_type in ['Weekly', 'Monthly', 'Yearly']:
        query = query.filter_by(is_active=True, timeframe=filter_type)
        
    # Apply Sorting
    if sort_by == 'category':
        query = query.order_by(Step.category)
    else:
        query = query.order_by(Step.created_at.desc())
        
    steps = query.all()
    return render_template('steps_list.html', steps=steps, filter_type=filter_type)

@app.route('/create_step', methods=['POST'])
@login_required
def create_step():
    title = request.form.get('title')
    category = request.form.get('category')
    timeframe = request.form.get('timeframe')
    mode = request.form.get('deadline_mode')
    
    if title:
        deadline = calculate_deadline(timeframe, mode)
        
        new_step = Step(
            title=title, 
            category=category, 
            timeframe=timeframe, 
            deadline_mode=mode,
            deadline_date=deadline,
            user=current_user
        )
        db.session.add(new_step)
        db.session.commit()
        flash(f"Goal created!", 'success')
        
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/step/<int:step_id>', methods=['GET', 'POST'])
@login_required
def step_view(step_id):
    step = Step.query.get_or_404(step_id)
    if step.user_id != current_user.id:
        return redirect(url_for('dashboard'))

    today = date.today()
    todays_log = StepLog.query.filter_by(step_id=step.id, date=today).first()

    if request.method == 'POST':
        if 'overview_content' in request.form:
            step.overview_content = request.form['overview_content']
            flash('Saved.', 'success')
        elif 'log_content' in request.form:
            content = request.form['log_content']
            if todays_log:
                todays_log.content = content
            else:
                db.session.add(StepLog(content=content, step=step, user=current_user, date=today))
            flash('Logged!', 'success')
        db.session.commit()
        return redirect(url_for('step_view', step_id=step.id))

    history = StepLog.query.filter_by(step_id=step.id).order_by(StepLog.date.desc()).all()
    return render_template('step_view.html', step=step, todays_log=todays_log, history=history)

@app.route('/step/edit/<int:step_id>', methods=['POST'])
@login_required
def edit_step(step_id):
    step = Step.query.get_or_404(step_id)
    if step.user_id != current_user.id:
        return redirect(url_for('dashboard'))
    
    step.title = request.form.get('title')
    step.category = request.form.get('category')
    new_timeframe = request.form.get('timeframe')
    new_mode = request.form.get('deadline_mode')
    
    if new_timeframe != step.timeframe or new_mode != step.deadline_mode:
        step.timeframe = new_timeframe
        step.deadline_mode = new_mode
        step.deadline_date = calculate_deadline(new_timeframe, new_mode)
        
    db.session.commit()
    flash("Updated!", "success")
    return redirect(url_for('step_view', step_id=step.id))

@app.route('/step/delete/<int:step_id>')
@login_required
def delete_step(step_id):
    step = Step.query.get_or_404(step_id)
    if step.user_id == current_user.id:
        db.session.delete(step)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/step/archive/<int:step_id>')
@login_required
def archive_step(step_id):
    step = Step.query.get_or_404(step_id)
    if step.user_id == current_user.id:
        step.is_active = False
        db.session.commit()
    return redirect(url_for('dashboard'))

# ==========================================
#             SUBTASK ROUTES
# ==========================================

@app.route('/step/<int:step_id>/add_subtask', methods=['POST'])
@login_required
def add_subtask(step_id):
    step = Step.query.get_or_404(step_id)
    text = request.form.get('subtask_text')
    
    if text and step.user_id == current_user.id:
        db.session.add(SubTask(text=text, step=step))
        db.session.commit()
        
    return redirect(url_for('step_view', step_id=step.id))

@app.route('/toggle_subtask/<int:subtask_id>', methods=['POST'])
@login_required
def toggle_subtask(subtask_id):
    subtask = SubTask.query.get_or_404(subtask_id)
    if subtask.step.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    subtask.is_completed = not subtask.is_completed
    
    if subtask.is_completed:
        today = date.today()
        # Check for existing log to prevent duplicates
        if not StepLog.query.filter_by(step_id=subtask.step.id, date=today).first():
            db.session.add(StepLog(content=f"✅ {subtask.text}", step=subtask.step, user=current_user, date=today))
            flash("Streak updated!", "success")
        
        update_streak_status(current_user)
        
    db.session.commit()
    return jsonify({'success': True})

@app.route('/delete_subtask/<int:subtask_id>')
@login_required
def delete_subtask(subtask_id):
    subtask = SubTask.query.get_or_404(subtask_id)
    if subtask.step.user_id == current_user.id:
        step_id = subtask.step.id
        db.session.delete(subtask)
        db.session.commit()
        return redirect(url_for('step_view', step_id=step_id))
    return redirect(url_for('dashboard'))

# ==========================================
#             JOURNAL ROUTES
# ==========================================

@app.route('/journal', methods=['POST'])
@login_required
def update_global_journal():
    today = date.today()
    content = request.form.get('content')
    title = request.form.get('title', f"Entry for {today.strftime('%B %d')}")
    
    journal = GlobalJournal.query.filter_by(user_id=current_user.id, date=today).first()
    
    if journal:
        journal.content = content
        journal.title = title
    else:
        db.session.add(GlobalJournal(content=content, title=title, user=current_user, date=today))
        
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/journal_history')
@login_required
def journal_history():
    journals = GlobalJournal.query.filter_by(user_id=current_user.id).order_by(GlobalJournal.date.desc()).all()
    return render_template('journal_history.html', journals=journals, now=datetime.utcnow())

@app.route('/journal/edit/<int:id>', methods=['POST'])
@login_required
def edit_old_journal(id):
    journal = GlobalJournal.query.get_or_404(id)
    if journal.user_id != current_user.id:
        return redirect(url_for('dashboard'))
    
    if (datetime.utcnow() - journal.created_at).total_seconds() > 172800:
        flash("Too old to edit.", "error")
        return redirect(url_for('journal_history'))
        
    journal.content = request.form.get('content')
    journal.title = request.form.get('title')
    db.session.commit()
    return redirect(url_for('journal_history'))

# ==========================================
#             USER SETTINGS
# ==========================================

@app.route('/adjust_target/<string:action>')
@login_required
def adjust_target(action):
    if action == 'increase':
        current_user.daily_target += 1
    elif action == 'decrease' and current_user.daily_target > 1:
        current_user.daily_target -= 1
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/toggle_theme', methods=['POST'])
@login_required
def toggle_theme():
    current_user.is_dark_mode = not current_user.is_dark_mode
    db.session.commit()
    return jsonify({'dark_mode': current_user.is_dark_mode})

# ==========================================
#             AUTHENTICATION
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(request.args.get('next') or url_for('dashboard'))
        flash('Invalid details.', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('Username taken.', 'error')
        else:
            hashed_pw = generate_password_hash(password)
            # Make sure Rest Days column is handled in models, handled here by default=None
            new_user = User(username=username, password=hashed_pw, is_dark_mode=True)
            db.session.add(new_user)
            db.session.commit()
            return redirect(url_for('login'))
            
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)