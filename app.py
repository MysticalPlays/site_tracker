from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key_change_this'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

socketio = SocketIO(app, cors_allowed_origins="*")

# --- DATABASE CONNECTION ---
# !!! PASTE YOUR REAL MONGODB CONNECTION STRING HERE !!!
MONGO_URI = "mongodb+srv://ruchit:ruchit@cluster0.e0zayc3.mongodb.net/?retryWrites=true&w=majority"
client = MongoClient(MONGO_URI)
db = client['construction_db']

# --- USER MODEL ---
class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.role = user_data.get('role', 'user')

@login_manager.user_loader
def load_user(user_id):
    try:
        u = db.users.find_one({"_id": ObjectId(user_id)})
        if not u: return None
        return User(u)
    except: return None

# --- AUTH ROUTES ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        admin_code = request.form.get('admin_code')
        
        if db.users.find_one({"username": username}):
            flash("Username already exists")
            return redirect(url_for('register'))
        
        role = 'user'
        if admin_code == "MASTER_BUILDER":
            role = 'admin'

        hashed_password = generate_password_hash(password)
        db.users.insert_one({
            "username": username, 
            "password": hashed_password,
            "role": role
        })
        flash(f"Account created! Role: {role}")
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user_data = db.users.find_one({"username": username})
        
        if user_data and check_password_hash(user_data['password'], password):
            user_obj = User(user_data)
            login_user(user_obj)
            return redirect(url_for('dashboard'))
        flash("Invalid login")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- SITE MANAGEMENT ROUTES ---
@app.route('/api/sites')
@login_required
def get_sites():
    sites = list(db.sites.find())
    for site in sites:
        site['_id'] = str(site['_id'])
    return jsonify(sites)

@app.route('/api/create_site', methods=['POST'])
@login_required
def create_site():
    if current_user.role != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    
    data = request.json
    site_name = data.get('site_name')
    site_location = data.get('site_location')
    
    if not site_name or not site_location: return jsonify({"error": "Missing fields"}), 400
    
    new_site = {
        "name": site_name, 
        "location": site_location,
        "created_by": current_user.username
    }
    res = db.sites.insert_one(new_site)
    new_site['_id'] = str(res.inserted_id)
    
    socketio.emit('site_created', new_site)
    
    return jsonify({"success": True, "site": new_site})

# --- DASHBOARD ---
@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

@app.route('/api/get_materials/<site_id>')
@login_required
def get_materials(site_id):
    materials = list(db.materials.find({"site_id": site_id}))
    for mat in materials: mat['_id'] = str(mat['_id'])
    return jsonify(materials)

# --- REAL TIME ---
@socketio.on('join_site')
def handle_join(data):
    if 'site_id' in data and data['site_id']:
        join_room(data['site_id'])

@socketio.on('leave_site')
def handle_leave(data):
    if 'site_id' in data and data['site_id']:
        pass

@socketio.on('add_material')
def handle_add(data):
    site_id = data['site_id']
    unit_val = data.get('unit', '') 

    new_item = {
        "site_id": site_id,
        "name": data['name'],
        "quantity": data['quantity'],
        "unit": unit_val,
        "added_by": current_user.username,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    res = db.materials.insert_one(new_item)
    new_item['_id'] = str(res.inserted_id)
    emit('update_list', new_item, room=site_id)

# --- NEW DELETE FUNCTION ---
@socketio.on('delete_material')
def handle_delete(data):
    material_id = data.get('id')
    site_id = data.get('site_id')

    if not material_id: return

    # Find item to check permissions
    item = db.materials.find_one({"_id": ObjectId(material_id)})
    if not item: return

    # Permission Check: Is Admin OR Is Owner?
    is_admin = current_user.role == 'admin'
    is_owner = current_user.username == item.get('added_by')

    if is_admin or is_owner:
        db.materials.delete_one({"_id": ObjectId(material_id)})
        # Broadcast deletion to everyone in the room
        emit('item_deleted', {"id": material_id}, room=site_id)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)