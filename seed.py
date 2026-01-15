import os
import random
from pymongo import MongoClient
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()

# --- Configuration ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/parkease")
client = MongoClient(MONGO_URI)
db = client.get_database()
if db.name == 'test' and 'parkease' in MONGO_URI:
    db = client['parkease']

def seed_data():
    """
    Clears existing collections and populates the database with 50+ Mumbai locations,
    variable parking levels, and a demo user.
    """
    print(f"--- Seeding Database: {db.name} ---")

    # 1. Clear Existing Data
    db.users.drop()
    db.parking_areas.drop()
    db.bookings.drop()
    db.slots.drop()
    db.notifications.drop()
    db.slot_preferences.drop()
    print("Cleared existing collections.")

    # 2. Seed Demo User
    users_data = [
        {
            "email": "admin@parkease.com",
            "password": generate_password_hash("adminpassword", method='pbkdf2:sha256'),
            "full_name": "Admin User",
            "is_admin": True,
            "vehicle_number": "MH-01-AD-0001",
            "created_at": datetime.utcnow()
        },
        {
            "email": "demo1@gmail.com",
            "password": generate_password_hash("demo_1", method='pbkdf2:sha256'),
            "full_name": "Demo User One",
            "is_admin": False,
            "vehicle_number": "MH-03-BK-9999",
            "vehicle_type": "Car",
            "created_at": datetime.utcnow()
        },
        {
            "email": "manager@parkease.com",
            "password": generate_password_hash("managerpassword", method='pbkdf2:sha256'),
            "full_name": "Area Manager",
            "is_admin": False,
            "vehicle_number": "MH-04-MG-5555",
            "vehicle_type": "Bike",
            "created_at": datetime.utcnow()
        }
    ]
    db.users.insert_many(users_data)
    print("Inserted admin and demo1 users.")

    # 3. 50+ Real-Life Mumbai Locations
    mumbai_spots = [
        # South Mumbai
        {"name": "Gateway of India Plaza", "pos": [72.8347, 18.9220], "price": 100},
        {"name": "Colaba Causeway Market", "pos": [72.8300, 18.9150], "price": 40},
        {"name": "Nariman Point Business Lot", "pos": [72.8208, 18.9256], "price": 120},
        {"name": "Marine Drive North", "pos": [72.8236, 18.9431], "price": 80},
        {"name": "Crawford Market Parking", "pos": [72.8361, 18.9472], "price": 50},
        {"name": "Fashion Street South", "pos": [72.8295, 18.9395], "price": 60},
        {"name": "Churchgate Station Gate 1", "pos": [72.8270, 18.9350], "price": 70},
        {"name": "Wankhede Stadium Parking", "pos": [72.8250, 18.9380], "price": 90},
        {"name": "Girgaon Chowpatty Lot", "pos": [72.8140, 18.9540], "price": 50},
        {"name": "Malabar Hill Public Park", "pos": [72.8050, 18.9580], "price": 60},
        {"name": "Hanging Gardens Entrance", "pos": [72.8055, 18.9565], "price": 40},
        {"name": "Worli Seaface South", "pos": [72.8155, 19.0150], "price": 50},
        {"name": "Mahalaxmi Race Course", "pos": [72.8190, 18.9830], "price": 60},
        {"name": "Lower Parel - Kamala Mills", "pos": [72.8270, 18.9950], "price": 100},
        # Central Mumbai
        {"name": "Phoenix Palladium Mall", "pos": [72.8256, 18.9936], "price": 150},
        {"name": "Siddhivinayak Temple Lot", "pos": [72.8310, 19.0170], "price": 40},
        {"name": "Dadar Station East", "pos": [72.8467, 19.0178], "price": 40},
        {"name": "Shivaji Park Central", "pos": [72.8380, 19.0270], "price": 30},
        {"name": "Matunga King Circle", "pos": [72.8540, 19.0270], "price": 40},
        {"name": "Sion Hospital Road", "pos": [72.8630, 19.0380], "price": 30},
        {"name": "Kurla Station West", "pos": [72.8800, 19.0700], "price": 30},
        {"name": "Byculla Zoo Parking", "pos": [72.8340, 18.9790], "price": 40},
        # Western Suburbs
        {"name": "BKC Jio World Drive", "pos": [72.8634, 19.0645], "price": 200},
        {"name": "BKC ICICI Bank Tower", "pos": [72.8660, 19.0630], "price": 150},
        {"name": "Bandra Reclamation", "pos": [72.8250, 19.0435], "price": 50},
        {"name": "Bandra Linking Road", "pos": [72.8340, 19.0600], "price": 100},
        {"name": "Bandra Carter Road", "pos": [72.8210, 19.0680], "price": 80},
        {"name": "Juhu Beach North", "pos": [72.8270, 19.0980], "price": 70},
        {"name": "Airport T1 Domestic", "pos": [72.8530, 19.0900], "price": 150},
        {"name": "Airport T2 International", "pos": [72.8745, 19.0974], "price": 200},
        {"name": "Andheri Shoppers Stop", "pos": [72.8450, 19.1190], "price": 60},
        {"name": "Andheri Lokhandwala", "pos": [72.8260, 19.1350], "price": 60},
        {"name": "Versova Metro Station", "pos": [72.8120, 19.1310], "price": 50},
        {"name": "Infinity Mall Andheri", "pos": [72.8310, 19.1415], "price": 80},
        {"name": "Goregaon Hub Mall", "pos": [72.8580, 19.1550], "price": 50},
        {"name": "Oberoi Mall Goregaon", "pos": [72.8606, 19.1738], "price": 100},
        {"name": "Inorbit Mall Malad", "pos": [72.8350, 19.1850], "price": 80},
        {"name": "Kandivali Growels 101", "pos": [72.8680, 19.2050], "price": 70},
        {"name": "Borivali National Park", "pos": [72.8600, 19.2200], "price": 50},
        {"name": "Dahisar Check Naka", "pos": [72.8700, 19.2500], "price": 40},
        # Eastern Suburbs
        {"name": "Phoenix Market City Kurla", "pos": [72.8890, 19.0880], "price": 100},
        {"name": "R-City Mall Ghatkopar", "pos": [72.9164, 19.0997], "price": 80},
        {"name": "Powai Hiranandani Lot", "pos": [72.9100, 19.1170], "price": 80},
        {"name": "IIT Bombay Main Gate", "pos": [72.9100, 19.1250], "price": 50},
        {"name": "Vikhroli Godrej One", "pos": [72.9280, 19.1000], "price": 60},
        {"name": "Mulund LBS Marg", "pos": [72.9550, 19.1750], "price": 50},
        {"name": "Chembur K-Star Mall", "pos": [72.8980, 19.0530], "price": 50},
        # Navi Mumbai & Thane
        {"name": "Vashi Inorbit Mall", "pos": [72.9994, 19.0664], "price": 60},
        {"name": "Seawoods Grand Central", "pos": [73.0180, 19.0220], "price": 80},
        {"name": "Nerul Station Lot", "pos": [73.0170, 19.0330], "price": 20},
        {"name": "CBD Belapur Station", "pos": [73.0380, 19.0180], "price": 20},
        {"name": "Viviana Mall Thane", "pos": [72.9744, 19.2073], "price": 80},
        {"name": "Korum Mall Thane", "pos": [72.9680, 19.2020], "price": 60},
        {"name": "Thane Station East", "pos": [72.9780, 19.1850], "price": 30},
        {"name": "Kalyan Station West", "pos": [73.1300, 19.2300], "price": 20},
        {"name": "Panvel Mega Mall", "pos": [73.1150, 18.9900], "price": 30}
    ]

    parking_areas = []
    for loc in mumbai_spots:
        capacity = random.randint(50, 250)
        
        # Determine levels based on capacity
        if capacity < 70: levels = 1
        elif capacity < 120: levels = 2
        elif capacity < 180: levels = 3
        else: levels = 4

        parking_areas.append({
            "name": loc["name"],
            "capacity": capacity,
            "occupied": 0,
            "price": loc["price"],
            "has_ev": random.choice([True, False]),
            "has_handicap": True,
            "has_bike": True,
            "levels": levels,
            "location": {"type": "Point", "coordinates": loc["pos"]}
        })

    inserted_areas = db.parking_areas.insert_many(parking_areas)
    area_ids = inserted_areas.inserted_ids
    db.parking_areas.create_index([("location", "2dsphere")])
    print(f"Inserted {len(area_ids)} areas with variable floor levels.")

    # 3.1 Assign Manager to First Area
    first_area_id = area_ids[0]
    db.users.update_one({"email": "manager@parkease.com"}, {"$set": {"managed_area_id": first_area_id}})
    print(f"Assigned manager@parkease.com to area ID: {first_area_id}")


    # 4. Seed Slots (Cars and Bikes)
    print("Seeding slots across levels...")
    all_slots = []
    for idx, area_id in enumerate(area_ids):
        area_meta = parking_areas[idx]
        total_cap = area_meta["capacity"]
        levels = area_meta["levels"]
        
        # 70% Car, 30% Bike
        car_cap = int(total_cap * 0.7)
        bike_cap = total_cap - car_cap
        
        # Car Slots across levels
        car_per_level = car_cap // levels
        for level in range(1, levels + 1):
            count = car_per_level + (car_cap % levels if level == levels else 0)
            for num in range(1, count + 1):
                all_slots.append({
                    "area_id": area_id,
                    "level": level,
                    "slot_number": f"L{level}-C{num:02d}",
                    "is_bike": False,
                    "is_ev": area_meta["has_ev"] and num <= 5,
                    "is_handicap": num > 5 and num <= 8
                })
        
        # Bike Slots (always on Level 1)
        for num in range(1, bike_cap + 1):
            all_slots.append({
                "area_id": area_id,
                "level": 1,
                "slot_number": f"B-{num:02d}",
                "is_bike": True,
                "is_ev": False,
                "is_handicap": False
            })

    if all_slots:
        db.slots.insert_many(all_slots)
        db.slots.create_index([("area_id", 1), ("slot_number", 1)])
    
    print(f"Total slots seeded: {len(all_slots)}")
    print("Database seeding completed successfully!")

if __name__ == "__main__":
    seed_data()