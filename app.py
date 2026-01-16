from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
# ADDED CustomRestDay to imports
from models import db, User, Step, StepLog, GlobalJournal, SubTask, CustomRestDay
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
def get_next_birthday(dob):
    today = date.today()
    # Try to put birthday in current year
    try:
        this_year_bday = date(today.year, dob.month, dob.day)
    except ValueError:
        # Handle Leap Year babies (Feb 29) -> Feb 28
        this_year_bday = date(today.year, 2, 28)

    if this_year_bday >= today:
        return this_year_bday
    else:
        # If birthday passed, return next year
        try:
            return date(today.year + 1, dob.month, dob.day)
        except ValueError:
            return date(today.year + 1, 2, 28)

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

def get_custom_rest_days_map(user):
    """
    Returns a dictionary of custom rest days: {'2025-04-23': 'My Birthday', ...}
    """
    days = CustomRestDay.query.filter_by(user_id=user.id).all()
    data = {}
    for d in days:
        data[d.date.strftime('%Y-%m-%d')] = d.reason
    return data

def calculate_deadline(timeframe, mode):
    today = date.today()
    
    # 1. Handle "Custom" or Missing Mode
    if not mode or mode == 'custom':
        # Fallback if logic fails elsewhere
        return today + timedelta(days=7)

    # 2. Rolling Deadlines (Standard)
    if mode == 'rolling':
        if timeframe == 'Weekly': return today + timedelta(days=7)
        if timeframe == 'Monthly': return today + timedelta(days=30)
        if timeframe == 'Yearly': return today + timedelta(days=365)
    
    # 3. Calendar Deadlines (End of Period)
    elif mode == 'calendar':
        if timeframe == 'Weekly': return today + timedelta(days=(6 - today.weekday() + 7) % 7)
        if timeframe == 'Monthly': return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
        if timeframe == 'Yearly': return date(today.year, 12, 31)
            
    return today + timedelta(days=7) # Ultimate Fallback

def update_streak_status(user):
    """
    Handles logic for Daily Streaks, Freezes, and Rest Days (Weekly & Custom).
    """
    today = date.today()
    
    # Check if we need to reset, freeze, or apply rest day logic
    if user.last_streak_date:
        delta = (today - user.last_streak_date).days
        
        # If delta > 1, it means they missed at least one full day
        if delta > 1:
            yesterday = today - timedelta(days=1)
            
            # 1. Check Weekly Rest Days (Sat/Sun)
            is_weekly_rest = False
            if user.rest_days and str(yesterday.weekday()) in user.rest_days.split(','):
                is_weekly_rest = True
            
            # 2. Check Custom Rest Days (Birthday/Holiday)
            is_custom_rest = CustomRestDay.query.filter_by(user_id=user.id, date=yesterday).first()
            
            if is_weekly_rest or is_custom_rest:
                # Do nothing (Streak is safe because yesterday was planned rest)
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
#              MAIN ROUTES
# ==========================================

@app.route('/')
@login_required
def dashboard():
    today = date.today()
    progress = update_streak_status(current_user)
    
    steps = Step.query.filter_by(user_id=current_user.id, is_active=True).limit(4).all()
    daily_journal = GlobalJournal.query.filter_by(user_id=current_user.id, date=today).first()
    
    heatmap_data = get_heatmap_data(current_user)
    
    # NEW: Fetch Custom Rest Days
    custom_rest_days = get_custom_rest_days_map(current_user)
    
    # Check if TODAY is a special day (for the journal UI)
    today_str = today.strftime('%Y-%m-%d')
    special_day_reason = custom_rest_days.get(today_str)
    
    return render_template(
        'dashboard.html', 
        user=current_user, 
        steps=steps, 
        daily_journal=daily_journal,
        progress=progress,
        today_date=today,
        heatmap_data=heatmap_data,
        custom_rest_days=custom_rest_days,
        special_day_reason=special_day_reason,
        user_created_at=current_user.created_at.strftime('%Y-%m-%d')
    )

# ==========================================
#          PHASE 4: NEW FEATURES
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

@app.route('/settings/add_custom_date', methods=['POST'])
@login_required
def add_custom_date():
    """
    Adds a specific date (like a birthday) as a rest day.
    """
    date_str = request.form.get('date')
    reason = request.form.get('reason')
    
    if date_str and reason:
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            # Check duplicate
            exists = CustomRestDay.query.filter_by(user_id=current_user.id, date=date_obj).first()
            if not exists:
                db.session.add(CustomRestDay(date=date_obj, reason=reason, user=current_user))
                db.session.commit()
                flash(f"Marked {date_str} as Rest Day: {reason}", "success")
            else:
                flash("You already have a plan for that day!", "info")
        except ValueError:
            flash("Invalid date format.", "error")
            
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
    # Fetch logs
    logs = StepLog.query.filter_by(user_id=current_user.id).order_by(StepLog.date.desc()).all()
    
    # Group logs by date
    logs_by_date = {}
    for log in logs:
        d_str = log.date.strftime('%Y-%m-%d')
        if d_str not in logs_by_date: logs_by_date[d_str] = []
        logs_by_date[d_str].append(log)
        
    return render_template('calendar_view.html', logs_by_date=logs_by_date)

@app.route('/about')
def about():
    return render_template('about.html')

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
    
    # Inputs from the corrected form
    timeframe = request.form.get('timeframe') # Weekly, Monthly, Yearly
    deadline_mode = request.form.get('deadline_mode') # rolling, calendar
    
    if title:
        # Calculate deadline based on the mode
        deadline = calculate_deadline(timeframe, deadline_mode)
        
        new_step = Step(
            title=title, 
            category=category, 
            timeframe=timeframe, 
            deadline_mode=deadline_mode,
            deadline_date=deadline,
            user=current_user
        )
        db.session.add(new_step)
        db.session.commit()
        
        flash(f"Goal created! Deadline: {deadline.strftime('%b %d, %Y')}", 'success')
        
    return redirect(request.referrer or url_for('dashboard'))

# ==========================================
#          STEP VIEW, LOGS & EDITING
# ==========================================

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
            # Updating Knowledge Base
            step.overview_content = request.form['overview_content']
            flash('Knowledge Base updated.', 'success')
        
        elif 'log_content' in request.form:
            # Creating/Updating Daily Log
            content = request.form['log_content']
            if todays_log:
                todays_log.content = content
            else:
                db.session.add(StepLog(content=content, step=step, user=current_user, date=today))
            flash('Logged successfully!', 'success')
            
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
    
    # 1. Update Basic Fields
    step.title = request.form.get('title')
    step.category = request.form.get('category')
    
    # 2. Check if Timing Changed
    new_timeframe = request.form.get('timeframe')
    new_mode = request.form.get('deadline_mode')
    
    # Only recalculate deadline if parameters changed
    if new_timeframe != step.timeframe or new_mode != step.deadline_mode:
        step.timeframe = new_timeframe
        step.deadline_mode = new_mode
        step.deadline_date = calculate_deadline(new_timeframe, new_mode)
        flash(f"Updated! New deadline: {step.deadline_date.strftime('%Y-%m-%d')}", "success")
    else:
        flash("Details updated.", "success")
        
    db.session.commit()
    return redirect(url_for('step_view', step_id=step.id))

@app.route('/step/delete/<int:step_id>')
@login_required
def delete_step(step_id):
    step = Step.query.get_or_404(step_id)
    if step.user_id == current_user.id:
        db.session.delete(step)
        db.session.commit()
        flash(f'Step "{step.title}" deleted forever.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/step/archive/<int:step_id>')
@login_required
def archive_step(step_id):
    step = Step.query.get_or_404(step_id)
    if step.user_id == current_user.id:
        step.is_active = False
        db.session.commit()
        flash(f'Step "{step.title}" marked as Accomplished!', 'success')
    return redirect(url_for('dashboard'))

# ==========================================
#             SUBTASK LOGIC
# ==========================================

@app.route('/step/<int:step_id>/add_subtask', methods=['POST'])
@login_required
def add_subtask(step_id):
    step = Step.query.get_or_404(step_id)
    if step.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    text = request.form.get('subtask_text')
    if text:
        new_task = SubTask(text=text, step=step)
        db.session.add(new_task)
        db.session.commit()
    
    return redirect(url_for('step_view', step_id=step.id))

@app.route('/toggle_subtask/<int:subtask_id>', methods=['POST'])
@login_required
def toggle_subtask(subtask_id):
    subtask = SubTask.query.get_or_404(subtask_id)
    if subtask.step.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Toggle Status
    subtask.is_completed = not subtask.is_completed
    
    # AUTOMATIC LOGGING LOGIC
    if subtask.is_completed:
        today = date.today()
        # Check if log exists for today
        existing_log = StepLog.query.filter_by(step_id=subtask.step.id, date=today).first()
        
        if not existing_log:
            # Create a log automatically
            auto_msg = f"✅ Completed subtask: {subtask.text}"
            db.session.add(StepLog(content=auto_msg, step=subtask.step, user=current_user, date=today))
            flash("Subtask completed! Streak updated automatically. 🔥", "success")
        
        update_streak_status(current_user)

    db.session.commit()
    return jsonify({'success': True, 'is_completed': subtask.is_completed})

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
#          JOURNAL & PREFERENCES
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
    now = datetime.utcnow()
    return render_template('journal_history.html', journals=journals, now=now)

@app.route('/journal/edit/<int:id>', methods=['POST'])
@login_required
def edit_old_journal(id):
    journal = GlobalJournal.query.get_or_404(id)
    if journal.user_id != current_user.id:
        return redirect(url_for('dashboard'))
    
    # 48 Hour Edit Lock
    diff = datetime.utcnow() - journal.created_at
    if diff.total_seconds() > 172800:
        flash("Cannot edit entries older than 48 hours.", "error")
        return redirect(url_for('journal_history'))

    journal.content = request.form.get('content')
    journal.title = request.form.get('title')
    db.session.commit()
    flash("Entry updated.", "success")
    return redirect(url_for('journal_history'))

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

# --- USER PROFILE & ACCOUNT MANAGEMENT ---

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)

@app.route('/update_password', methods=['POST'])
@login_required
def update_password():
    current_pw = request.form.get('current_password')
    new_pw = request.form.get('new_password')
    
    if not check_password_hash(current_user.password, current_pw):
        flash("Current password incorrect.", "error")
        return redirect(url_for('profile'))
        
    current_user.password = generate_password_hash(new_pw)
    db.session.commit()
    flash("Password updated successfully.", "success")
    return redirect(url_for('profile'))

@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    confirmation = request.form.get('confirmation')
    
    if confirmation == current_user.username:
        user = User.query.get(current_user.id)
        
        # Delete all associated data manually (Cascade usually handles this, but good to be safe)
        StepLog.query.filter_by(user_id=user.id).delete()
        GlobalJournal.query.filter_by(user_id=user.id).delete()
        CustomRestDay.query.filter_by(user_id=user.id).delete()
        
        steps = Step.query.filter_by(user_id=user.id).all()
        for s in steps:
            SubTask.query.filter_by(step_id=s.id).delete()
            db.session.delete(s)
            
        db.session.delete(user)
        db.session.commit()
        
        logout_user()
        flash("Your account has been deleted. We're sad to see you go.", "info")
        return redirect(url_for('login'))
    else:
        flash("Username confirmation did not match.", "error")
        return redirect(url_for('profile'))

@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    # Get Data
    new_name = request.form.get('name')
    new_email = request.form.get('email')
    new_dob_str = request.form.get('dob')
    
    # Check if email is taken by someone else
    existing_email = User.query.filter_by(email=new_email).first()
    if existing_email and existing_email.id != current_user.id:
        flash("Email already in use.", "error")
        return redirect(url_for('profile'))

    # Update Fields
    current_user.name = new_name
    current_user.email = new_email
    
    if new_dob_str:
        current_user.dob = datetime.strptime(new_dob_str, '%Y-%m-%d').date()
    
    db.session.commit()
    flash("Profile details updated.", "success")
    return redirect(url_for('profile'))

# ==========================================
#             AUTHENTICATION
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        
        flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        name = request.form.get('name')
        password = request.form.get('password')
        dob_str = request.form.get('dob') # "2006-04-23"
        
        # Check Username/Email
        if User.query.filter((User.username==username) | (User.email==email)).first():
            flash('Username or Email already taken.', 'error')
        else:
            hashed_pw = generate_password_hash(password)
            
            # Parse Date of Birth
            dob_date = None
            if dob_str:
                dob_date = datetime.strptime(dob_str, '%Y-%m-%d').date()

            new_user = User(
                username=username, 
                email=email,
                name=name,
                dob=dob_date,
                password=hashed_pw, 
                is_dark_mode=True
            )
            db.session.add(new_user)
            db.session.flush() # Flush to get new_user.id before committing relationships

            # AUTOMATION: Create Birthday Rest Day
            if dob_date:
                next_bday = get_next_birthday(dob_date)
                # Create the rest day entry
                bday_rest = CustomRestDay(
                    date=next_bday,
                    reason=f"🎉 {name}'s Birthday!",
                    user_id=new_user.id
                )
                db.session.add(bday_rest)

            db.session.commit()
            flash('Account created! Birthday rest day assigned. 🎂', 'success')
            return redirect(url_for('login'))
            
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ==========================================
#              MAIN EXECUTION
# ==========================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)