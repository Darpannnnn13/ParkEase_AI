import os
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from datetime import datetime, timedelta
import uuid
from flask_pymongo import PyMongo
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
from dotenv import load_dotenv
from flask_socketio import SocketIO, emit, join_room, leave_room

# --- App Initialization ---
load_dotenv()
app = Flask(__name__)

# --- Configuration ---
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

if not app.config["MONGO_URI"] or not app.config["SECRET_KEY"]:
    raise Exception("MONGO_URI and SECRET_KEY must be set in .env file")

# --- Extensions ---
mongo = PyMongo(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_page"
socketio = SocketIO(app, cors_allowed_origins="*")

# --- User Model ---
class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data["_id"])
        self.email = user_data["email"]
        self.full_name = user_data["full_name"]
        self.is_admin = user_data.get("is_admin", False)
        self.vehicle_number = user_data.get("vehicle_number", "Not Set")
        self.vehicle_type = user_data.get("vehicle_type", "Car")
        self.is_ev = user_data.get("is_ev", False)
        self.accessibility = user_data.get("accessibility", False)
        self.managed_area_id = str(user_data.get("managed_area_id")) if user_data.get("managed_area_id") else None

@login_manager.user_loader
def load_user(user_id):
    user_data = mongo.db.users.find_one({"_id": ObjectId(user_id)})
    return User(user_data) if user_data else None

@app.context_processor
def inject_notifications():
    if current_user.is_authenticated and not current_user.is_admin and not current_user.managed_area_id:
        count = mongo.db.notifications.count_documents({
            "user_id": ObjectId(current_user.id),
            "read": False
        })
        return dict(notification_count=count)
    return dict(notification_count=0)

# --- Helper Functions ---
def check_no_shows(area_id=None):
    """Checks for confirmed bookings that missed the 15-minute grace period."""
    now = datetime.utcnow()
    query = {
        "status": "Confirmed",
        "grace_period_end": {"$lt": now}
    }
    if area_id:
        query["area_id"] = area_id
    expired_bookings = list(mongo.db.bookings.find(query))

    for booking in expired_bookings:
        amount = booking.get("amount", 0)
        refund = amount * 0.90
        
        mongo.db.bookings.update_one(
            {"_id": booking["_id"]},
            {
                "$set": {
                    "status": "Cancelled (No Show)",
                    "refund_amount": refund,
                    "cancellation_reason": "Grace period expired"
                }
            }
        )
        
        spots = booking.get("spots", 1)
        area_update = mongo.db.parking_areas.find_one_and_update(
            {"_id": booking["area_id"]},
            {"$inc": {"occupied": -spots}},
            return_document=True
        )
        
        slots_to_release = booking.get('slot_ids', [])
        area_name = booking.get('area_name')
        area_id = booking.get('area_id')
        
        for slot_id in slots_to_release:
            level = int(slot_id.split('-')[0].replace('L', '')) if '-' in slot_id else 1
            preferences = mongo.db.slot_preferences.find({"area_id": area_id, "level": level})
            for pref in preferences:
                mongo.db.notifications.insert_one({
                    "user_id": pref["user_id"],
                    "message": f"A slot on Level {level} at {area_name} is now available (from a no-show).",
                    "timestamp": datetime.utcnow(),
                    "read": False
                })
                socketio.emit('new_notification', room=str(pref["user_id"]))

        if area_update:
            socketio.emit('update_availability', {
                'area_id': str(area_update["_id"]),
                'occupied': area_update["occupied"],
                'capacity': area_update["capacity"]
            })

def check_expiry_reminders():
    """Checks for active bookings ending soon (within 15 mins) and sends alerts."""
    now = datetime.utcnow()
    upcoming_end = now + timedelta(minutes=15)
    
    # Find active bookings ending soon that haven't been reminded
    expiring_bookings = list(mongo.db.bookings.find({
        "status": "Active",
        "end_time": {"$lte": upcoming_end, "$gt": now},
        "reminder_sent": {"$ne": True}
    }))
    
    for booking in expiring_bookings:
        mongo.db.notifications.insert_one({
            "user_id": booking["user_id"],
            "message": f"⏱️ Time is running out! Your parking session at {booking['area_name']} ends in less than 15 minutes.",
            "timestamp": datetime.utcnow(),
            "read": False
        })
        mongo.db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"reminder_sent": True}})
        socketio.emit('new_notification', room=str(booking["user_id"]))

# --- Page Routes ---

@app.route("/")
def index():
    check_no_shows()
    check_expiry_reminders()
    areas = list(mongo.db.parking_areas.find({}, {"name": 1}))
    return render_template("index.html", areas=areas)

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('user_dashboard' if not current_user.is_admin else 'admin_dashboard'))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user_data = mongo.db.users.find_one({"email": email})

        if user_data and check_password_hash(user_data["password"], password):
            user = User(user_data)
            login_user(user)
            flash("Logged in successfully!", "success")
            if user.is_admin:
                return redirect(url_for("admin_dashboard"))
            elif user.managed_area_id:
                return redirect(url_for("manager_dashboard"))
            else:
                return redirect(url_for("user_dashboard"))
        else:
            flash("Invalid email or password.", "error")
            
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "POST":
        email = request.form.get("email")
        full_name = request.form.get("fullname")
        password = request.form.get("password")

        if mongo.db.users.find_one({"email": email}):
            flash("Email address already exists.", "error")
            return redirect(url_for("register_page"))

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        mongo.db.users.insert_one({
            "email": email,
            "full_name": full_name,
            "password": hashed_password,
            "is_admin": False,
            "created_at": datetime.utcnow()
        })
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login_page"))

    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login_page"))

@app.route("/dashboard")
@login_required
def user_dashboard():
    if current_user.is_admin or current_user.managed_area_id:
        return redirect(url_for('admin_dashboard'))
    
    check_no_shows()
    check_expiry_reminders()
    bookings = list(mongo.db.bookings.find({"user_id": ObjectId(current_user.id)}).sort("start_time", -1))
    
    # Find the user's most recent active booking to help them find their vehicle
    last_active_booking = mongo.db.bookings.find_one(
        {"user_id": ObjectId(current_user.id), "status": "Active"},
        sort=[("check_in_time", -1)]
    )
    
    # Enrichment for display
    for booking in bookings:
        if "start_time" not in booking:
            booking["start_time"] = booking.get("booking_time", datetime.utcnow())
        if "slot_ids" not in booking:
            booking["slot_ids"] = [booking.get("slot_id", "N/A")]
            
    return render_template("user_dashboard.html", bookings=bookings, last_active_booking=last_active_booking)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        update_data = {
            "full_name": request.form.get("full_name"),
            "vehicle_number": request.form.get("vehicle_number"),
            "vehicle_type": request.form.get("vehicle_type"),
            "is_ev": True if request.form.get("is_ev") else False,
            "accessibility": True if request.form.get("accessibility") else False
        }
        mongo.db.users.update_one({"_id": ObjectId(current_user.id)}, {"$set": update_data})
        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")

@app.route("/book", methods=["POST"])
@login_required
def book_spot():
    """Handles parking slot reservation for single or multiple selected slots (Cars and Bikes)."""
    area_id = request.form.get("area_id")
    booking_time_str = request.form.get("booking_time")
    duration = int(request.form.get("duration", 1))
    selected_slot_ids_str = request.form.get("slot_id") 
    
    if current_user.is_admin or current_user.managed_area_id:
        flash("Administrators and Managers are not allowed to book parking spots.", "error")
        return redirect(url_for("index"))

    # 1. Basic Validation
    if not area_id or not booking_time_str:
        flash("Please select an area and time.", "error")
        return redirect(url_for("index"))

    # Convert comma-separated string from UI into a list
    selected_slot_ids = [s.strip() for s in selected_slot_ids_str.split(',') if s.strip()] if selected_slot_ids_str else []
    spots = len(selected_slot_ids)

    if spots == 0:
        flash("Please select at least one parking slot from the map.", "error")
        return redirect(url_for("index"))

    # 2. Profile Check (Enforce vehicle number)
    if not current_user.vehicle_number or current_user.vehicle_number == "Not Set":
        flash("Please add your vehicle number in your profile before booking.", "error")
        return redirect(url_for("profile"))

    # 3. Time Processing
    try:
        start_time = datetime.strptime(booking_time_str, "%Y-%m-%dT%H:%M")
        end_time = start_time + timedelta(hours=duration)
    except ValueError:
        flash("Invalid date format.", "error")
        return redirect(url_for("index"))

    # 4. Area and Collision Check
    area = mongo.db.parking_areas.find_one({"_id": ObjectId(area_id)})
    if not area:
        flash("Parking area not found.", "error")
        return redirect(url_for("index"))

    # Verify if any of the selected slots are already booked for this window
    collision = mongo.db.bookings.find_one({
        "area_id": ObjectId(area_id),
        "status": {"$in": ["Active", "Pending Payment", "Confirmed"]},
        "start_time": {"$lt": end_time},
        "end_time": {"$gt": start_time},
        "slot_ids": {"$in": selected_slot_ids}
    })

    if collision:
        flash(f"One or more selected slots are already taken for this time window.", "error")
        return redirect(url_for("index"))
    
    # 5. Dynamic Pricing Logic (Bike Discount)
    base_price = area.get("price", 20)
    total_amount = 0
    
    for slot_id in selected_slot_ids:
        # Check if it's a Bike slot (B- prefix)
        if slot_id.startswith('B-'):
            # Bikes get a 50% discount as they take less space
            slot_price = base_price * 0.5 
        else:
            slot_price = base_price
        
        total_amount += (slot_price * duration)
    
    # 6. Create Booking Document
    booking_token = uuid.uuid4().hex[:8].upper()
    exit_token = uuid.uuid4().hex[:8].upper()
    booking_doc = {
        "user_id": ObjectId(current_user.id),
        "area_id": ObjectId(area_id),
        "area_name": area["name"],
        "start_time": start_time,
        "end_time": end_time,
        "grace_period_end": start_time + timedelta(minutes=15),
        "duration": duration,
        "spots": spots,
        "status": "Pending Payment",
        "slot_ids": selected_slot_ids,
        "amount": round(total_amount, 2),
        "booking_token": booking_token,
        "exit_token": exit_token,
        "coordinates": area["location"]["coordinates"],
        "vehicle_number": current_user.vehicle_number,
        "created_at": datetime.utcnow()
    }
    mongo.db.bookings.insert_one(booking_doc)

    # 7. Update Area Occupancy
    # Note: For simplicity, 1 bike slot counts as 1 spot in total capacity
    new_occupied = area.get("occupied", 0) + spots
    mongo.db.parking_areas.update_one(
        {"_id": ObjectId(area_id)},
        {"$set": {"occupied": new_occupied}}
    )

    # 8. Real-time Notification for Map
    socketio.emit('update_availability', {
        'area_id': str(area_id),
        'occupied': new_occupied,
        'capacity': area["capacity"]
    })

    flash(f"Booking pending for {spots} slot(s). Total: ₹{round(total_amount, 2)}", "success")
    flash("Note: A 10% fee applies if you cancel after payment is confirmed.", "info")
    return redirect(url_for("user_dashboard"))

@app.route("/pay/<booking_id>", methods=["POST"])
@login_required
def pay_booking(booking_id):
    """Confirm payment. Preserves the user-selected slot_ids."""
    booking = mongo.db.bookings.find_one_and_update(
        {"_id": ObjectId(booking_id), "user_id": ObjectId(current_user.id)},
        {"$set": {"status": "Confirmed"}},
        return_document=True
    )
    
    if booking:
        mongo.db.notifications.insert_one({
            "user_id": ObjectId(current_user.id),
            "message": f"✅ Booking Confirmed! You have reserved spots at {booking['area_name']}.",
            "timestamp": datetime.utcnow(),
            "read": False
        })
        socketio.emit('new_notification', room=str(current_user.id))

    flash("Payment successful! Your spots are now secured.", "success")
    return redirect(url_for("user_dashboard"))

@app.route("/cancel_booking/<booking_id>", methods=["POST"])
@login_required
def cancel_booking(booking_id):
    booking = mongo.db.bookings.find_one({"_id": ObjectId(booking_id), "user_id": ObjectId(current_user.id)})

    if not booking:
        flash("Booking not found.", "error")
        return redirect(url_for("user_dashboard"))

    # Handle cancellation based on status
    if booking['status'] == 'Pending Payment':
        # Just delete it, no financial transaction yet
        mongo.db.bookings.delete_one({"_id": ObjectId(booking_id)})
        mongo.db.parking_areas.update_one(
            {"_id": booking["area_id"]},
            {"$inc": {"occupied": -booking.get('spots', 1)}}
        )
        flash("Booking canceled successfully.", "success")

    elif booking['status'] == 'Confirmed':
        # Apply 10% cancellation fee
        amount = booking.get("amount", 0)
        refund = amount * 0.90
        
        mongo.db.bookings.update_one(
            {"_id": booking["_id"]},
            {"$set": {
                "status": "Cancelled (User)",
                "refund_amount": round(refund, 2),
                "cancellation_reason": "User cancelled after payment."
            }}
        )
        mongo.db.parking_areas.update_one(
            {"_id": booking["area_id"]},
            {"$inc": {"occupied": -booking.get('spots', 1)}}
        )
        flash(f"Booking canceled. A 10% fee was applied. ₹{round(refund, 2)} will be refunded.", "info")
    
    else:
        flash(f"Cannot cancel booking with status '{booking['status']}'.", "error")

    # Real-time update for map
    area_update = mongo.db.parking_areas.find_one({"_id": booking["area_id"]})
    if area_update:
        socketio.emit('update_availability', {
            'area_id': str(area_update["_id"]),
            'occupied': area_update["occupied"],
            'capacity': area_update["capacity"]
        })

    return redirect(url_for("user_dashboard"))

@app.route("/extend_booking/<booking_id>", methods=["POST"])
@login_required
def extend_booking(booking_id):
    booking = mongo.db.bookings.find_one({"_id": ObjectId(booking_id), "user_id": ObjectId(current_user.id)})
    if not booking:
        flash("Booking not found.", "error")
        return redirect(url_for("user_dashboard"))

    try:
        hours_to_extend = int(request.form.get("hours", 1))
        area = mongo.db.parking_areas.find_one({"_id": booking["area_id"]})
        base_price = area.get("price", 20)
        
        extension_cost = 0
        for slot_id in booking.get("slot_ids", []):
            slot_price = base_price * 0.5 if slot_id.startswith('B-') else base_price
            extension_cost += (slot_price * hours_to_extend)

        new_end_time = booking["end_time"] + timedelta(hours=hours_to_extend)

        # Check for collision with future bookings for the same slots
        collision = mongo.db.bookings.find_one({
            "area_id": booking["area_id"],
            "slot_ids": {"$in": booking.get("slot_ids", [])},
            "status": {"$in": ["Active", "Pending Payment", "Confirmed"]},
            "_id": {"$ne": ObjectId(booking_id)},
            "start_time": {"$lt": new_end_time},
            "end_time": {"$gt": booking["end_time"]}
        })

        if collision:
            flash("Cannot extend, another booking starts soon after yours.", "error")
            return redirect(url_for("user_dashboard"))

        mongo.db.bookings.update_one(
            {"_id": ObjectId(booking_id)},
            {
                "$set": {"end_time": new_end_time},
                "$inc": {"amount": round(extension_cost, 2), "duration": hours_to_extend}
            }
        )
        flash(f"Booking extended by {hours_to_extend} hour(s). Additional cost: ₹{round(extension_cost, 2)}", "success")

    except Exception as e:
        flash(f"Could not extend booking: {e}", "error")

    return redirect(url_for("user_dashboard"))

# --- API & Admin Routes ---

@app.route("/api/availability")
def get_availability():
    areas = list(mongo.db.parking_areas.find({}))
    for area in areas:
        area["_id"] = str(area["_id"])
    return jsonify(areas)

@app.route("/api/notifications")
@login_required
def get_notifications():
    now = datetime.utcnow()
    notifications = list(mongo.db.notifications.find({
        "user_id": ObjectId(current_user.id)
    }).sort("timestamp", -1).limit(10))
    
    for n in notifications:
        n["_id"] = str(n["_id"])
        n["time_ago"] = (now - n["timestamp"]).total_seconds()

    # Mark as read after fetching
    mongo.db.notifications.update_many(
        {"user_id": ObjectId(current_user.id), "read": False},
        {"$set": {"read": True}}
    )
    return jsonify(notifications)

@app.route("/api/area/<area_id>/slots")
def get_area_slots(area_id):
    start_time_str = request.args.get("start_time")
    duration = int(request.args.get("duration", 1))
    
    try:
        start_time = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M")
        end_time = start_time + timedelta(hours=duration)
    except:
        start_time, end_time = datetime.utcnow(), datetime.utcnow() + timedelta(hours=1)

    slots = list(mongo.db.slots.find({"area_id": ObjectId(area_id)}).sort([("level", 1), ("slot_number", 1)]))
    
    # Find occupied slots: Fetch Active bookings (to check overstay) AND overlapping future bookings
    query = {
        "area_id": ObjectId(area_id),
        "$or": [
            {"status": "Active"},
            {
                "status": {"$in": ["Pending Payment", "Confirmed"]},
                "start_time": {"$lt": end_time}, 
                "end_time": {"$gt": start_time}
            }
        ]
    }
    occupied_bookings = list(mongo.db.bookings.find(query))
    
    occupied_set = set()
    for b in occupied_bookings:
        # If Active, extend effective end time to NOW to handle overstays
        b_end = max(b["end_time"], datetime.utcnow()) if b["status"] == "Active" else b["end_time"]
        
        # Check overlap (Active bookings might not have been filtered by time in query)
        if b["start_time"] < end_time and b_end > start_time:
            for s_id in b.get("slot_ids", []):
                occupied_set.add(s_id)
    
    for slot in slots:
        slot["_id"] = str(slot["_id"])
        slot["area_id"] = str(slot["area_id"])
        slot["status"] = "Occupied" if slot["slot_number"] in occupied_set else "Available"
        
    return jsonify(slots)

@app.route("/manager/dashboard")
@login_required
def manager_dashboard():
    if not current_user.managed_area_id:
        flash("Access denied. You are not a parking manager.", "error")
        return redirect(url_for("index"))
    
    area_id = ObjectId(current_user.managed_area_id)
    area = mongo.db.parking_areas.find_one({"_id": area_id})
    
    if not area:
        flash("Assigned parking area not found.", "error")
        return redirect(url_for("index"))

    check_no_shows(area_id)

    # --- Booking Lists ---
    requested_bookings = list(mongo.db.bookings.find({"area_id": area_id, "status": "Confirmed"}).sort("start_time", 1))
    active_bookings_list = list(mongo.db.bookings.find({"area_id": area_id, "status": "Active"}).sort("check_in_time", -1))

    # Enrich
    for booking in requested_bookings + active_bookings_list:
        user = mongo.db.users.find_one({"_id": booking["user_id"]})
        booking["user_email"] = user["email"] if user else "Unknown"
        booking["is_bike_booking"] = any(s.startswith('B-') for s in booking.get("slot_ids", []))

    return render_template("manager_dashboard.html", 
                           area=area,
                           requested_bookings=requested_bookings,
                           active_bookings_list=active_bookings_list)

@app.route("/manager/analytics")
@login_required
def manager_analytics():
    if not current_user.managed_area_id:
        flash("Access denied. You are not a parking manager.", "error")
        return redirect(url_for("index"))
    
    area_id = ObjectId(current_user.managed_area_id)
    area = mongo.db.parking_areas.find_one({"_id": area_id})
    
    if not area:
        flash("Assigned parking area not found.", "error")
        return redirect(url_for("index"))

    # --- Area Specific Stats ---
    pipeline = [
        {"$match": {"area_id": area_id, "status": {"$in": ["Active", "Confirmed", "Completed"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    revenue_result = list(mongo.db.bookings.aggregate(pipeline))
    revenue = revenue_result[0]['total'] if revenue_result else 0
    
    active_bookings_count = mongo.db.bookings.count_documents({"area_id": area_id, "status": "Active"})
    occupancy = int((area["occupied"] / area["capacity"]) * 100) if area["capacity"] > 0 else 0

    # --- Calendar & Analytics Data ---
    # Group bookings by date for the calendar
    pipeline = [
        {"$match": {
            "area_id": area_id, 
            "status": {"$in": ["Active", "Confirmed", "Completed"]},
            "start_time": {"$exists": True, "$ne": None}
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$start_time"}},
            "daily_revenue": {"$sum": "$amount"},
            "daily_bookings": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    calendar_data = list(mongo.db.bookings.aggregate(pipeline))
    
    return render_template("manager_analytics.html", 
                           area=area,
                           revenue=revenue,
                           active_bookings_count=active_bookings_count,
                           occupancy=occupancy,
                           calendar_data=calendar_data)

@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash("You must be an admin to view this page.", "error")
        return redirect(url_for("index"))

    # --- Global Stats ---
    pipeline = [
        {"$match": {"status": {"$in": ["Active", "Confirmed"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    revenue_result = list(mongo.db.bookings.aggregate(pipeline))
    revenue = revenue_result[0]['total'] if revenue_result else 0
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    new_users_count = mongo.db.users.count_documents({"created_at": {"$gte": today_start}})

    areas = list(mongo.db.parking_areas.find({}))
    
    # Enrich areas with manager info
    for area in areas:
        manager = mongo.db.users.find_one({"managed_area_id": area["_id"]})
        area["manager_email"] = manager["email"] if manager else "Not Assigned"

    return render_template("admin_dashboard.html",
                           revenue=revenue,
                           new_users=new_users_count,
                           areas=areas)

@app.route("/admin/area/<area_id>")
@login_required
def admin_area_details(area_id):
    if not current_user.is_admin:
        flash("Access denied.", "error")
        return redirect(url_for("index"))
    
    area = mongo.db.parking_areas.find_one({"_id": ObjectId(area_id)})
    if not area:
        flash("Area not found.", "error")
        return redirect(url_for("admin_dashboard"))

    # Get Current Manager
    manager = mongo.db.users.find_one({"managed_area_id": ObjectId(area_id)})

    # --- Analytics Data (Same as Manager View) ---
    pipeline = [
        {"$match": {"area_id": ObjectId(area_id), "status": {"$in": ["Active", "Confirmed", "Completed"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    revenue_result = list(mongo.db.bookings.aggregate(pipeline))
    revenue = revenue_result[0]['total'] if revenue_result else 0
    
    active_bookings_count = mongo.db.bookings.count_documents({"area_id": ObjectId(area_id), "status": "Active"})
    occupancy = int((area["occupied"] / area["capacity"]) * 100) if area["capacity"] > 0 else 0

    # Calendar Data
    pipeline = [
        {"$match": {
            "area_id": ObjectId(area_id), 
            "status": {"$in": ["Active", "Confirmed", "Completed"]},
            "start_time": {"$exists": True, "$ne": None}
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$start_time"}},
            "daily_revenue": {"$sum": "$amount"},
            "daily_bookings": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    calendar_data = list(mongo.db.bookings.aggregate(pipeline))

    return render_template("admin_area_details.html", 
                           area=area,
                           manager=manager,
                           revenue=revenue,
                           active_bookings_count=active_bookings_count,
                           occupancy=occupancy,
                           calendar_data=calendar_data)

@app.route("/admin/assign_manager", methods=["POST"])
@login_required
def assign_manager():
    if not current_user.is_admin:
        return redirect(url_for('index'))
        
    area_id = request.form.get("area_id")
    email = request.form.get("manager_email")
    
    user = mongo.db.users.find_one({"email": email})
    if not user:
        flash(f"User with email '{email}' not found.", "error")
        return redirect(url_for('admin_area_details', area_id=area_id))
    
    # Remove manager from this area if one exists (optional cleanup)
    mongo.db.users.update_one({"managed_area_id": ObjectId(area_id)}, {"$unset": {"managed_area_id": ""}})
    
    # Assign new manager
    mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"managed_area_id": ObjectId(area_id)}})
    
    flash(f"Successfully assigned {email} as manager.", "success")
    return redirect(url_for('admin_area_details', area_id=area_id))

@app.route("/admin/trigger_no_show", methods=["POST"])
@login_required
def trigger_no_show_check():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    
    check_no_shows()
    flash("No-show check completed.", "info")
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/add_area", methods=["POST"])
@login_required
def add_parking_area():
    if not current_user.is_admin:
        return redirect(url_for('index'))
        
    try:
        name = request.form.get("name")
        capacity = int(request.form.get("capacity"))
        lat = float(request.form.get("lat"))
        lng = float(request.form.get("lng"))
        manager_email = request.form.get("manager_email")

        area_doc = {
            "name": name,
            "capacity": capacity,
            "occupied": 0,
            "price": 50,
            "has_ev": True,
            "has_handicap": True,
            "has_bike": True,
            "location": {
                "type": "Point",
                "coordinates": [lng, lat]
            }
        }
        result = mongo.db.parking_areas.insert_one(area_doc)
        mongo.db.parking_areas.create_index([("location", "2dsphere")])
        
        # --- Generate Slots for the new Area ---
        area_id = result.inserted_id
        car_capacity = int(capacity * 0.7)
        bike_capacity = capacity - car_capacity
        new_slots = []

        # Car Slots (All on Level 1)
        for num in range(1, car_capacity + 1):
            new_slots.append({
                "area_id": area_id, "level": 1, "slot_number": f"C-{num:02d}",
                "is_bike": False, "is_ev": False, "is_handicap": False
            })

        # Bike Slots (All on Level 1)
        for num in range(1, bike_capacity + 1):
            new_slots.append({
                "area_id": area_id, "level": 1, "slot_number": f"B-{num:02d}",
                "is_bike": True, "is_ev": False, "is_handicap": False
            })

        if new_slots:
            mongo.db.slots.insert_many(new_slots)
            
        # --- Assign Manager ---
        if manager_email:
            manager = mongo.db.users.find_one({"email": manager_email})
            if manager:
                mongo.db.users.update_one({"_id": manager["_id"]}, {"$set": {"managed_area_id": area_id}})
                flash(f"Area added and assigned to {manager_email}.", "success")
            else:
                flash(f"Area added, but manager email '{manager_email}' not found.", "warning")
        else:
            flash(f"Area '{name}' added successfully.", "success")
    except Exception as e:
        flash(f"Error adding area: {e}", "error")
        
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/verify_booking", methods=["POST"])
@login_required
def verify_booking():
    if not current_user.managed_area_id:
        return redirect(url_for("index"))
    
    token = request.form.get("token", "").strip().upper()
    vehicle = request.form.get("vehicle_number", "").strip().upper()
    
    # Find booking by vehicle and (entry token OR exit token)
    booking = mongo.db.bookings.find_one({
        "vehicle_number": vehicle,
        "$or": [{"booking_token": token}, {"exit_token": token}]
    })

    if booking and str(booking["area_id"]) != current_user.managed_area_id:
        flash("You are not authorized to manage this booking.", "error")
        return redirect(url_for("manager_dashboard"))

    if booking:
        # Check-in Flow (Entry Token)
        if booking.get("booking_token") == token:
            if booking["status"] in ["Confirmed", "Pending Payment"]:
                mongo.db.bookings.update_one(
                    {"_id": booking["_id"]},
                    {"$set": {"status": "Active", "check_in_time": datetime.utcnow()}}
                )
                mongo.db.notifications.insert_one({
                    "user_id": booking["user_id"],
                    "message": f"🚗 Entry Confirmed at {booking['area_name']}. Your parking timer has started.",
                    "timestamp": datetime.utcnow(),
                    "read": False
                })
                socketio.emit('new_notification', room=str(booking["user_id"]))
                flash(f"Check-in successful for {vehicle}!", "success")
            elif booking["status"] == "Active":
                flash("This is an Entry Token. The session is already Active. Please use the Exit Token to check out.", "warning")
            else:
                flash(f"Cannot check-in. Status is {booking['status']}.", "error")
        
        # Check-out Flow (Exit Token)
        elif booking.get("exit_token") == token:
            if booking["status"] == "Active":
                mongo.db.bookings.update_one(
                    {"_id": booking["_id"]},
                    {"$set": {"status": "Completed", "check_out_time": datetime.utcnow()}}
                )
                # Release the spot
                area_update = mongo.db.parking_areas.find_one_and_update(
                    {"_id": booking["area_id"]},
                    {"$inc": {"occupied": -booking.get('spots', 1)}},
                    return_document=True
                )
                
                # Update Map Real-time
                if area_update:
                    socketio.emit('update_availability', {
                        'area_id': str(area_update["_id"]),
                        'occupied': area_update["occupied"],
                        'capacity': area_update["capacity"]
                    })
                    
                mongo.db.notifications.insert_one({
                    "user_id": booking["user_id"],
                    "message": f"👋 Exit Confirmed at {booking['area_name']}. Thank you for using ParkEase!",
                    "timestamp": datetime.utcnow(),
                    "read": False
                })
                socketio.emit('new_notification', room=str(booking["user_id"]))
                flash(f"Check-out successful for {vehicle}. Slot freed.", "success")
            elif booking["status"] in ["Confirmed", "Pending Payment"]:
                flash("This is an Exit Token. The session has not started yet. Please use the Entry Token to check in.", "warning")
            else:
                flash("Cannot check-out. Session is not active.", "error")
    else:
        flash("Invalid token or vehicle number.", "error")
    return redirect(url_for("manager_dashboard"))

@app.route("/notifications")
@login_required
def notifications_page():
    user_id = ObjectId(current_user.id)
    
    mongo.db.notifications.update_many(
        {"user_id": user_id, "read": False},
        {"$set": {"read": True}}
    )

    notifications = list(mongo.db.notifications.find({"user_id": user_id}).sort("timestamp", -1))
    
    preferences_cursor = mongo.db.slot_preferences.find({"user_id": user_id})
    preferences = []
    for pref in preferences_cursor:
        area = mongo.db.parking_areas.find_one({"_id": pref["area_id"]})
        if area:
            pref["area_name"] = area["name"]
            preferences.append(pref)

    all_areas = list(mongo.db.parking_areas.find({}, {"name": 1}))

    return render_template("notifications.html", 
                           notifications=notifications, 
                           preferences=preferences,
                           all_areas=all_areas)

@app.route("/set_preference", methods=["POST"])
@login_required
def set_preference():
    data = request.get_json()
    area_id = data.get("area_id")
    level = data.get("level")

    if not area_id or not level:
        return jsonify({"status": "error", "message": "Area and level required."}), 400

    mongo.db.slot_preferences.update_one(
        {"user_id": ObjectId(current_user.id), "area_id": ObjectId(area_id), "level": level},
        {"$set": {"timestamp": datetime.utcnow()}},
        upsert=True
    )
    return jsonify({"status": "success", "message": "Preference saved."})

@app.route("/remove_preference", methods=["POST"])
@login_required
def remove_preference():
    pref_id = request.form.get("pref_id")
    if pref_id:
        mongo.db.slot_preferences.delete_one({"_id": ObjectId(pref_id), "user_id": ObjectId(current_user.id)})
        flash("Preference removed.", "success")
    return redirect(url_for('notifications_page'))

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        join_room(str(current_user.id))

if __name__ == "__main__":
    socketio.run(app, debug=True, port=5000)
